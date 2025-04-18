# Plan: DULMS Scraper Web Interface

This plan outlines the steps to build a web interface for the DULMS scraper using FastAPI and Server-Sent Events (SSE) for live log streaming.

**Phase 1: Project Setup & Backend Foundation**

1.  **Create Project Structure:**
    *   Create a new top-level directory named `UniStackerWebApp`.
    *   Inside `UniStackerWebApp`, create the following:
        *   `backend/`: Directory for all Python backend code.
        *   `frontend/`: Directory for HTML, CSS, and JavaScript files.
        *   `requirements.txt`: File to list Python dependencies.
        *   `.gitignore`: (Recommended) To exclude virtual environments, logs, etc.

2.  **Refactor Selenium Script (`backend/dulms_public.py`):**
    *   Copy the existing `main.py` content into `backend/dulms_public.py`.
    *   **Encapsulate Logic:** Modify the script so the main scraping logic resides within a function (e.g., `run_dulms_scraper`). This function should accept `username`, `password`, `captcha_api_key`, and `discord_webhook` as arguments.
    *   **Parameterize Config:** Update the `CONFIG` dictionary (or relevant parts) within the function to use the passed-in arguments instead of hardcoded values or environment variables directly fetched within `main`.
    *   **Headless Mode:** Ensure Selenium is configured to run in headless mode (`options.add_argument("--headless=new")`).
    *   **Return Value:** The `run_dulms_scraper` function should return the final `scraped_data` dictionary upon successful completion or raise an exception on failure.
    *   **Logging for Streaming:**
        *   Modify the `logging` setup. Add a custom logging handler (e.g., a `QueueHandler` feeding a `queue.Queue` or a custom handler that directly pushes to an SSE stream manager/broadcaster). This is crucial for capturing logs from the Selenium script and making them available to the FastAPI SSE endpoint. We'll need a mechanism to pass this queue or handler reference to the `run_dulms_scraper` function.

3.  **Setup FastAPI Application (`backend/main.py`):**
    *   Install necessary libraries: `fastapi`, `uvicorn[standard]`, `selenium`, `requests`, `python-dotenv`, `sse-starlette` (for SSE), `pillow`. Update `requirements.txt`.
    *   Create a basic FastAPI app instance in `backend/main.py`.
    *   Implement a simple queue or broadcasting mechanism to hold/distribute log messages and the final result.

**Phase 2: API Endpoints & Background Task**

4.  **Scraper Trigger Endpoint (`POST /api/run-scraper`):**
    *   Define a Pydantic model for the request body (username, password, captcha key, webhook).
    *   Create an endpoint that accepts POST requests with the user's input data.
    *   Upon receiving a request:
        *   Instantiate the log queue/handler for this specific run.
        *   Use FastAPI's `BackgroundTasks` or `asyncio.create_task` to run the `run_dulms_scraper` function from `dulms_public.py` in the background, passing the user inputs and the log queue/handler.
        *   Store the final scraped data somewhere accessible once the task finishes (e.g., in-memory dictionary keyed by a task ID, or push it via SSE).
        *   Return an immediate JSON response like `{"message": "Scraping process initiated.", "task_id": "some_unique_id"}`.

5.  **Log Streaming Endpoint (`GET /api/stream-logs/{task_id}`):**
    *   Implement an endpoint using `sse-starlette`'s `EventSourceResponse`.
    *   This endpoint will continuously check the log queue associated with the `task_id` (or listen to the broadcaster).
    *   As log messages appear in the queue, it will format them (e.g., `{"type": "log", "data": "Log message here..."}`) and send them as SSE events to the connected client.
    *   When the scraper finishes, it can send a special event indicating completion or error (e.g., `{"type": "status", "data": "completed"}` or `{"type": "status", "data": "error", "message": "Error details"}`).
    *   When the results are ready, send them as another event (e.g., `{"type": "results", "data": scraped_data_json}`).

6.  **Static Files & CORS:**
    *   Configure FastAPI to serve static files (HTML, CSS, JS) from the `frontend/` directory.
    *   Configure CORS (Cross-Origin Resource Sharing) middleware if the frontend might be served from a different origin than the backend during development.

**Phase 3: Frontend Development**

7.  **HTML Structure (`frontend/index.html`):**
    *   Create the main HTML file.
    *   Add input fields (`<input>`) for username, password, captcha API key, and Discord webhook URL, properly labeled.
    *   Include a clear, concise section explaining how to create a Discord webhook URL (perhaps within a collapsible `<details>` tag).
    *   Add a button (`<button>`) to trigger the scraping process.
    *   Create container elements (`<div>`) to display status messages, the live log stream (`id="log-output"`), and the final results (`id="results-output"`). Link `style.css` and `script.js`.

8.  **CSS Styling (`frontend/style.css`):**
    *   Apply CSS rules to make the interface clean, readable, and visually appealing. Style the inputs, button, log area, and results area. Use flexbox or grid for layout.

9.  **JavaScript Logic (`frontend/script.js`):**
    *   Add an event listener to the "Run Scraper" button.
    *   **On Button Click:**
        *   Get the values from the input fields. Perform basic validation (e.g., check if fields are empty).
        *   Disable the button and show a "Processing..." status.
        *   Clear previous logs and results.
        *   Send a `POST` request to the `/api/run-scraper` endpoint with the input data as JSON.
        *   Handle the response: If successful, get the `task_id`.
    *   **Establish SSE Connection:**
        *   Create an `EventSource` instance connecting to `/api/stream-logs/{task_id}`.
    *   **Handle SSE Events:**
        *   Listen for `message` events (or custom named events if defined in the backend).
        *   Parse the incoming event `data` (which we decided will be JSON).
        *   If `event.data.type === 'log'`, append `event.data.data` to the `#log-output` div, ensuring it scrolls automatically.
        *   If `event.data.type === 'status'`, update the status message (e.g., "Completed", "Error: ..."). Re-enable the button if appropriate. Close the EventSource connection if 'completed' or 'error'.
        *   If `event.data.type === 'results'`, parse `event.data.data` and format/display it nicely within the `#results-output` div (e.g., create tables for assignments/quizzes per course).
    *   **Error Handling:** Implement `onerror` handlers for both the fetch request and the EventSource connection to display error messages to the user.

**Phase 4: Testing & Refinement**

10. **Run & Test:**
    *   Start the FastAPI backend using Uvicorn (`uvicorn backend.main:app --reload`).
    *   Open `index.html` in a browser (or navigate to the URL served by FastAPI).
    *   Test the full workflow with valid and invalid inputs.
    *   Verify log streaming, status updates, and final results display. Check error handling.
11. **Refine:** Adjust styling, improve error messages, and optimize the display of results based on testing.

**Mermaid Diagram:**

```mermaid
graph TD
    A[User Enters Data in Frontend] --> B{Run Scraper Button};
    B --> C[JS: POST /api/run-scraper (data)];
    C --> D[FastAPI: /api/run-scraper];
    D --> E{Start Scraper in Background Task (pass log queue/handler)};
    D -- task_id --> F[FastAPI: Return {message: 'Initiated', task_id}];
    F --> G[JS: Display 'Running...', Store task_id];
    G --> H[JS: Connect EventSource to /api/stream-logs/{task_id}];
    H --> I[FastAPI: /api/stream-logs/{task_id} (SSE Endpoint)];

    subgraph Scraper Background Task
        K[dulms_public.py Runs] --> L{Logs};
        K --> M{Scraped JSON Data};
        L --> N[Log Queue/Handler];
        M --> O[Result Notification];
    end

    E --> K;

    N --> I;
    I -- SSE Event {type:'log', data: ...} --> H;
    H --> P[JS: Append Log to UI];

    O --> I; # Scraper task notifies SSE endpoint upon completion/error
    I -- SSE Event {type:'status', data: 'completed'/'error'} --> H;
    I -- SSE Event {type:'results', data: ...} --> H;
    H --> Q[JS: Display Results in UI];
    H --> R[JS: Display 'Completed'/'Error', Close SSE];