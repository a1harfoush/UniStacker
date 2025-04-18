# UniStacker WebApp - DULMS Scraper

This application provides a web interface to scrape assignment and quiz data from the DULMS (Delta University Learning Management System) website. It displays the scraped data grouped by course and can optionally send upcoming deadline alerts to Discord.

![image](https://github.com/user-attachments/assets/166e685d-40c8-4e6f-ac6f-e7fc8b3651f3)


## Features

*   Web-based interface for easy input of credentials.
*   Scrapes assignments and quizzes from DULMS.
*   Displays results grouped by course with collapsible sections.
*   Live log streaming during the scraping process.
*   Uses [FreeCaptchaBypass](https://freecaptchabypass.com/cp/index) API to solve CAPTCHAs.
*   (Optional) Sends upcoming deadline notifications via Discord webhook.


## Tech Stack

This project is built using the following technologies:

- **Backend**: Python (FastAPI), Selenium
- **Frontend**: HTML, CSS, JavaScript
- **Automation**: Microsoft Edge WebDriver
- **APIs**: FreeCaptchaBypass API
- **Optional Notifications**: Discord Webhook

## Setup Instructions

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd UniStackerWebApp
    ```

2.  **Create and Activate a Python Virtual Environment:**
    *   It's highly recommended to use a virtual environment to manage dependencies.
    *   **Windows (cmd/powershell):**
        ```bash
        python -m venv .venv
        .venv\Scripts\activate
        ```
    *   **macOS/Linux:**
        ```bash
        python3 -m venv .venv
        source .venv/bin/activate
        ```

3.  **Install Dependencies:**
    *   Make sure your virtual environment is active.
    *   Install the required Python packages:
        ```bash
        pip install -r requirements.txt
        ```

4.  **Download Microsoft Edge WebDriver:**
    *   This script uses Selenium with Microsoft Edge. You need the corresponding `msedgedriver.exe`.
    *   Download the correct version for your Edge browser installation from the official site: [Microsoft Edge WebDriver Downloads](https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/)
    *   **Important:** Place the downloaded `msedgedriver.exe` file inside the `backend/` directory within the `UniStackerWebApp` folder.

5.  **Get Captcha API Key:**
    *   Sign up or log in at [FreeCaptchaBypass](https://freecaptchabypass.com/cp/index).
    *   Find and copy your API key (Client Key). You will need this when running the app.

6.  **(Optional) Get Discord Webhook URL:**
    *   If you want deadline notifications:
        *   Open Discord, go to your desired Server Settings > Integrations > Webhooks.
        *   Click "New Webhook", give it a name (e.g., "DULMS Alerts"), choose a channel.
        *   Click "Copy Webhook URL".

## Running the Application

1.  **Ensure your virtual environment is active.**
2.  **Make sure `msedgedriver.exe` is in the `backend/` directory.**
3.  **Start the FastAPI server:**
    *   From the `UniStackerWebApp` root directory, run:
        ```bash
        python -m uvicorn backend.main:app --reload --port 8000 --workers 1
        ```
    *   The `--reload` flag automatically restarts the server when code changes are detected (useful for development). The `--workers 1` flag is recommended for the simple in-memory task management used in this version.

4.  **Access the Web Interface:**
    *   Open your web browser and navigate to: `http://127.0.0.1:8000`

5.  **Use the App:**
    *   Enter your DULMS Username.
    *   Enter your DULMS Password.
    *   Enter your FreeCaptchaBypass API Key.
    *   (Optional) Enter your Discord Webhook URL.
    *   Click "Run Scraper".
    *   Observe the logs and wait for the results to appear.

## Project Structure

```
UniStackerWebApp/
├── backend/
│   ├── __pycache__/
│   ├── dulms_public.py # Refactored Selenium scraper logic
│   ├── main.py         # FastAPI application, API endpoints
│   ├── app.log         # Log file (ignored by git)
│   └── msedgedriver.exe # WebDriver (needs to be downloaded manually)
├── frontend/
│   ├── index.html      # Main HTML page
│   ├── script.js       # Frontend JavaScript logic
│   └── style.css       # CSS styling
├── .venv/              # Virtual environment (ignored by git)
├── .gitignore          # Specifies intentionally untracked files
├── README.md           # This file
└── requirements.txt    # Python dependencies
```

## Notes

*   Storing passwords directly is generally insecure. Consider using environment variables or a more secure configuration method for production use.
*   The scraper relies on the specific HTML structure of the DULMS website. Changes to the website may break the scraper.
*   The `msedgedriver.exe` version must match your installed Microsoft Edge browser version.
