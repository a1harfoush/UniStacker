import asyncio
import queue
import logging
import traceback
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
import os
from pathlib import Path

# Import the refactored scraper function
from .dulms_public import run_dulms_scraper

# --- Configuration ---
# Get the directory where this script is located
BACKEND_DIR = Path(__file__).parent
# Get the parent directory (UniStackerWebApp)
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
LOG_FILE_PATH = BACKEND_DIR / "app.log"

# --- Logging Setup (Basic for now, will enhance for SSE) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE_PATH), # Log to file
        logging.StreamHandler() # Also log to console (for uvicorn output)
    ]
)
logger = logging.getLogger(__name__)

# --- In-memory storage for task status and results (simple approach) ---
# For a production app, consider Redis or another persistent store
task_queues = {} # Dictionary to hold log queues for each task {task_id: queue.Queue}
task_results = {} # Dictionary to hold results {task_id: result_data_or_error}
task_statuses = {} # Dictionary to track status {task_id: "running" | "completed" | "error"}

# --- Pydantic Models ---
class ScraperInput(BaseModel):
    username: str
    password: str
    captcha_api_key: str
    discord_webhook: str | None = None # Optional

# --- FastAPI App ---
# Use lifespan context manager for startup/shutdown events if needed later
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     # Startup logic here
#     logger.info("Application starting...")
#     yield
#     # Shutdown logic here
#     logger.info("Application shutting down...")

app = FastAPI(title="UniStacker WebApp") # lifespan=lifespan

# --- Background Task Wrapper ---
# This runs in a separate thread via BackgroundTasks
def run_scraper_task_wrapper(task_id: str, inputs: ScraperInput):
    """
    Wrapper function to run the scraper, handle logging, status, and results.
    """
    log_queue = task_queues.get(task_id)
    if not log_queue:
        logger.error(f"Log queue not found for task {task_id} at start of wrapper.")
        task_statuses[task_id] = "error"
        task_results[task_id] = {"message": "Log queue setup failed before starting."}
        return

    task_statuses[task_id] = "running"
    logger.info(f"Background task started for task_id: {task_id}")
    final_message = ""

    try:
        # Run the actual scraper function (synchronous)
        result_data = run_dulms_scraper(
            log_queue=log_queue,
            username=inputs.username,
            password=inputs.password,
            captcha_api_key=inputs.captcha_api_key,
            discord_webhook=inputs.discord_webhook
        )
        task_results[task_id] = result_data
        task_statuses[task_id] = "completed"
        final_message = "Scraping task completed successfully."
        logger.info(f"Scraping task {task_id} completed successfully.")

    except Exception as e:
        logger.error(f"Scraping task {task_id} failed: {e}", exc_info=True)
        task_statuses[task_id] = "error"
        # Store error details for the client
        task_results[task_id] = {"message": f"An error occurred: {str(e)}"}
        final_message = f"Scraping task failed: {str(e)}"

    finally:
        # Add a final status message to the log queue
        if log_queue:
            log_queue.put(f"--- TASK STATUS ({task_id}): {task_statuses.get(task_id, 'unknown').upper()} ---")
            log_queue.put(final_message)
        logger.info(f"Background task finished for task_id: {task_id} with status: {task_statuses.get(task_id)}")


# --- API Endpoints ---

@app.post("/api/run-scraper")
async def trigger_scraper(scraper_input: ScraperInput, background_tasks: BackgroundTasks):
    """
    Triggers the DULMS scraper as a background task.
    """
    task_id = os.urandom(8).hex() # Generate a simple unique task ID
    logger.info(f"Received scraper request. Task ID: {task_id}")

    # TODO:
    # 1. Create a log queue for this task_id
    # 2. Start the actual scraper function in the background, passing the queue
    # 3. Store task status

    task_statuses[task_id] = "queued" # Or "running" immediately
    task_queues[task_id] = queue.Queue() # Simple synchronous queue for now

    # Add the wrapper function to background tasks
    background_tasks.add_task(run_scraper_task_wrapper, task_id, scraper_input)

    return {"message": "Scraping process initiated.", "task_id": task_id}

@app.get("/api/stream-logs/{task_id}")
async def stream_logs(request: Request, task_id: str):
    """
    Streams logs and results for a given task ID using SSE.
    """
    if task_id not in task_queues:
         # Or check task_statuses if queues are removed after completion
        raise HTTPException(status_code=404, detail="Task ID not found or already completed.")

    async def event_generator():
        log_queue = task_queues.get(task_id)
        if not log_queue:
            yield ServerSentEvent(data=json.dumps({"type": "error", "message": "Log stream unavailable."}))
            return

        try:
            while True:
                # Check connection closed
                if await request.is_disconnected():
                    logger.info(f"Client disconnected for task {task_id}.")
                    # Optionally clean up resources here if needed
                    # del task_queues[task_id]
                    # del task_results[task_id]
                    break

                # Get log from queue (non-blocking)
                try:
                    log_record = log_queue.get_nowait() # Get LogRecord object
                    # Format the LogRecord into a string message before sending
                    # Ensure we have handlers and a formatter to format the message
                    log_message = "Could not format log record." # Default message
                    if logger.handlers and hasattr(logger.handlers[0], 'formatter') and logger.handlers[0].formatter:
                         log_message = logger.handlers[0].formatter.format(log_record)
                    elif hasattr(log_record, 'getMessage'): # Fallback if formatter isn't easily accessible
                         log_message = log_record.getMessage()

                    # Send the formatted string
                    yield ServerSentEvent(data=json.dumps({"type": "log", "data": log_message.strip()}))
                except queue.Empty:
                    # No logs currently, check task status
                    current_status = task_statuses.get(task_id)
                    if current_status == "completed":
                        logger.info(f"Task {task_id} completed. Sending final status and results.")
                        result = task_results.get(task_id, {})
                        yield ServerSentEvent(data=json.dumps({"type": "results", "data": result}))
                        yield ServerSentEvent(data=json.dumps({"type": "status", "data": "completed"}))
                        break # End stream
                    elif current_status == "error":
                        logger.warning(f"Task {task_id} errored. Sending error status.")
                        error_details = task_results.get(task_id, {"message": "Unknown error"})
                        yield ServerSentEvent(data=json.dumps({"type": "status", "data": "error", "message": error_details.get("message")}))
                        break # End stream
                    else: # Still running or queued
                        await asyncio.sleep(0.5) # Wait before checking queue again

        except asyncio.CancelledError:
             logger.info(f"Log stream cancelled for task {task_id}.")
        except Exception as e:
            logger.error(f"Error in SSE generator for task {task_id}: {e}", exc_info=True)
            yield ServerSentEvent(data=json.dumps({"type": "error", "message": "Internal server error during streaming."}))
        finally:
            # Clean up queue and results for this task ID after streaming ends
            if task_id in task_queues:
                del task_queues[task_id]
            if task_id in task_results:
                del task_results[task_id]
            if task_id in task_statuses:
                del task_statuses[task_id] # Remove status tracking too
            logger.info(f"Cleaned up resources for task {task_id}.")


    return EventSourceResponse(event_generator())

# --- Static Files Serving ---
# Mount the frontend directory to serve static files (HTML, CSS, JS)
# Serve index.html at the root
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")

# (Placeholder function 'run_fake_scraper' removed)

# --- Main execution (for running with uvicorn) ---
if __name__ == "__main__":
    import uvicorn
    # Make sure to run from the 'UniStackerWebApp' directory:
    # python -m uvicorn backend.main:app --reload --port 8000
    # Or directly if in the backend dir:
    # uvicorn main:app --reload --port 8000
    # Note: Ensure the log queue is handled correctly on reload/multiple workers if not using a single worker
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, workers=1) # Recommend workers=1 for simple in-memory queue