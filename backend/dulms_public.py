import selenium
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.edge.options import Options
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    StaleElementReferenceException,
    ElementClickInterceptedException
)
import re
from PIL import Image
import base64
import io
import time
import requests
import os
import logging
import logging.handlers # Added for QueueHandler
import json
from datetime import datetime, timedelta
import traceback
import queue # Added for log queue
from pathlib import Path # Added for path handling

# --- Constants (Derived from original CONFIG) ---
# Using constants instead of a CONFIG dict within this module
# Construct absolute path assuming driver is in the same directory as this script
_SCRIPT_DIR = Path(__file__).parent
DRIVER_PATH = str(_SCRIPT_DIR / "msedgedriver.exe") # More robust path
LOGIN_URL = "https://dulms.deltauniv.edu.eg/Login.aspx"
QUIZZES_URL = "https://dulms.deltauniv.edu.eg/Quizzes/StudentQuizzes"
ASSIGNMENTS_URL = "https://dulms.deltauniv.edu.eg/Assignment/AssignmentStudentList"
LOGIN_SUCCESS_URL_PART = "Profile/StudentProfile"
# OUTPUT_FILE = "course_data_refactored.json" # Output handled by caller
# LOG_FILE = "dulms_scraper.log" # Logging handled by caller/QueueHandler
DEADLINE_THRESHOLD_DAYS = 3
MAX_LOGIN_RETRIES = 3
CAPTCHA_SOLVE_RETRIES = 3
DEFAULT_TIMEOUT = 20
POLL_FREQUENCY = 0.2

# --- Module Level Logger ---
# The handlers will be configured within run_dulms_scraper based on the queue
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO) # Set base level for the logger

# --- Selenium Driver Initialization ---
def initialize_driver(headless=True):
    """Initializes and returns a Selenium WebDriver instance."""
    logger.info("Initializing the Selenium driver...")
    options = Options()
    if headless:
        options.add_argument("--headless=new") # Ensure headless mode
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--log-level=3")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.page_load_strategy = 'normal'

    try:
        # Ensure the driver path exists before attempting to use it
        driver_executable_path = Path(DRIVER_PATH)
        if not driver_executable_path.is_file():
             logger.error(f"WebDriver executable not found at specified path: {DRIVER_PATH}")
             raise FileNotFoundError(f"WebDriver executable not found at: {DRIVER_PATH}")

        service = Service(executable_path=str(driver_executable_path)) # Use the absolute path
        driver = webdriver.Edge(service=service, options=options)
        logger.info("Driver initialized successfully.")
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        logger.error(f"Ensure '{DRIVER_PATH}' is in your PATH or the correct path is specified.")
        raise

# --- Utility Functions ---
def wait_for_element(driver, by, value, timeout=DEFAULT_TIMEOUT):
    """Waits for an element to be present and visible."""
    try:
        return WebDriverWait(driver, timeout, poll_frequency=POLL_FREQUENCY).until(
            EC.visibility_of_element_located((by, value))
        )
    except TimeoutException:
        logger.warning(f"Timeout waiting for element: {by}={value}")
        return None
    except Exception as e:
        logger.error(f"Error waiting for element {by}={value}: {e}")
        return None

def safe_find_element(parent, by, value):
    """Safely finds an element, returning None if not found."""
    try:
        return parent.find_element(by, value)
    except NoSuchElementException:
        return None

def safe_get_text(element):
    """Safely gets text from an element, returning empty string if None."""
    return element.text.strip() if element else ""

def click_element_robustly(driver, element, timeout=DEFAULT_TIMEOUT):
    """Attempts to click an element, handling potential issues."""
    if not element:
        logger.error("Attempted to click a None element.")
        return False
    try:
        clickable_element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(element))
        clickable_element.click()
        return True
    except ElementClickInterceptedException:
        logger.warning(f"Element click intercepted for {element.tag_name}. Trying JavaScript click.")
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(0.1)
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception as js_e:
            logger.error(f"JavaScript click also failed: {js_e}")
            return False
    except StaleElementReferenceException:
        logger.warning(f"Stale element reference during click. Element might need re-locating.")
        return False
    except TimeoutException:
        logger.error(f"Timeout waiting for element to be clickable: {element.tag_name}")
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            time.sleep(0.1)
            driver.execute_script("arguments[0].click();", element)
            logger.info("Used JS click as fallback after clickable timeout.")
            return True
        except Exception as js_e:
            logger.error(f"Fallback JavaScript click also failed after timeout: {js_e}")
            return False
    except Exception as e:
        logger.error(f"Failed to click element: {e}")
        return False

def dismiss_notifications(driver):
    """Checks for and dismisses any pop-up notifications."""
    try:
        notification_lock = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".announcement-lock"))
        )
        if notification_lock:
            dismiss_btn = safe_find_element(driver, By.CSS_SELECTOR, ".dismiss")
            if dismiss_btn and dismiss_btn.is_displayed():
                logger.info("Dismissing notification...")
                if click_element_robustly(driver, dismiss_btn, timeout=5):
                     time.sleep(0.5)
                else:
                     logger.warning("Failed to click dismiss button for notification.")
    except TimeoutException:
        logger.info("No notifications found or timeout waiting for notification lock.")
    except Exception as e:
        logger.warning(f"Error checking/dismissing notifications: {e}")


# --- CAPTCHA Solving ---
def solve_captcha_api(api_key, image_base64, retries=CAPTCHA_SOLVE_RETRIES):
    """Solves CAPTCHA using the FreeCaptchaBypass API with retry logic."""
    for attempt in range(retries):
        try:
            logger.info(f"Solving CAPTCHA via API (attempt {attempt + 1}/{retries})...")
            api_url_create = "https://freecaptchabypass.com/createTask"
            task_payload = {
                "clientKey": api_key,
                "task": {
                    "type": "ImageToTextTask",
                    "body": image_base64
                }
            }
            response_create = requests.post(api_url_create, json=task_payload, timeout=15)
            response_create.raise_for_status()
            task_result = response_create.json()

            if task_result.get("errorId") == 0:
                task_id = task_result.get("taskId")
                if not task_id:
                     raise Exception("API Error: Task ID not received.")
                logger.info(f"CAPTCHA task created successfully. Task ID: {task_id}")

                api_url_result = "https://freecaptchabypass.com/getTaskResult"
                result_payload = {"clientKey": api_key, "taskId": task_id}

                for poll_attempt in range(10): # Poll for ~20 seconds
                    time.sleep(2)
                    logger.debug(f"Polling for CAPTCHA result (Task ID: {task_id}, Attempt: {poll_attempt+1})")
                    response_result = requests.post(api_url_result, json=result_payload, timeout=10)
                    response_result.raise_for_status()
                    result_data = response_result.json()

                    status = result_data.get("status")
                    logger.debug(f"CAPTCHA status: {status} (Task ID: {task_id})")
                    if status == "ready":
                        solution = result_data.get("solution", {}).get("text")
                        if solution:
                            logger.info(f"CAPTCHA solved successfully: '{solution}'")
                            return solution
                        else:
                            error_desc = result_data.get('errorDescription', "Solution text not found in 'ready' status.")
                            raise Exception(f"API Error (Task ID: {task_id}): {error_desc}")
                    elif status == "processing":
                        continue
                    else:
                        error_desc = result_data.get('errorDescription', f'Unknown API error or status "{status}"')
                        raise Exception(f"API Error (Task ID: {task_id}): Status '{status}'. Description: {error_desc}")

                raise TimeoutException(f"CAPTCHA solution timed out after polling for 20 seconds (Task ID: {task_id}).")

            else:
                error_desc = task_result.get('errorDescription', 'Unknown API error during task creation')
                raise Exception(f"API Error during task creation: {error_desc}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during CAPTCHA API call: {e}")
        except TimeoutException as e:
             logger.error(f"CAPTCHA API polling timeout: {e}")
        except Exception as e:
            logger.error(f"Error solving CAPTCHA: {e}")

        if attempt < retries - 1:
            logger.warning(f"CAPTCHA solve failed, retrying in 3 seconds...")
            time.sleep(3)
        else:
            logger.error("CAPTCHA solve failed after all retries.")
            raise Exception("Failed to solve CAPTCHA after multiple attempts.")

def get_captcha_image_base64(driver):
    """Captures the CAPTCHA image and returns it as a base64 encoded string."""
    logger.info("Capturing CAPTCHA image...")
    try:
        captcha_img_element = WebDriverWait(driver, DEFAULT_TIMEOUT).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "div.captach img"))
        )

        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", captcha_img_element)
            time.sleep(0.2)
            img_bytes = captcha_img_element.screenshot_as_png
            logger.info("Captured CAPTCHA using element screenshot.")
            return base64.b64encode(img_bytes).decode('utf-8')
        except Exception as element_shot_error:
            logger.warning(f"Element screenshot failed ({element_shot_error}), falling back to page screenshot crop.")
            device_pixel_ratio = driver.execute_script("return window.devicePixelRatio;")
            location = captcha_img_element.location_once_scrolled_into_view
            size = captcha_img_element.size

            if not size['width'] or not size['height']:
                 logger.error("CAPTCHA element has zero size. Cannot capture.")
                 raise ValueError("CAPTCHA element has zero size.")

            left = int(location['x'] * device_pixel_ratio)
            top = int(location['y'] * device_pixel_ratio)
            right = int((location['x'] + size['width']) * device_pixel_ratio)
            bottom = int((location['y'] + size['height']) * device_pixel_ratio)
            box = (left, top, right, bottom)

            full_screenshot_png = driver.get_screenshot_as_png()
            image = Image.open(io.BytesIO(full_screenshot_png)).crop(box).convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            logger.info("Captured CAPTCHA using page screenshot crop.")
            return base64.b64encode(buffer.getvalue()).decode('utf-8')

    except TimeoutException:
        logger.error("Timeout waiting for CAPTCHA image element.")
        raise
    except Exception as e:
        logger.error(f"Failed to capture CAPTCHA image: {e}")
        raise

# --- Login ---
# Modified to use constants and logger
def login(driver, username, password, api_key, max_retries=MAX_LOGIN_RETRIES):
    """Logs into the university system with CAPTCHA solving and retries."""
    logger.info(f"Attempting login for user: {username}")
    for attempt in range(max_retries):
        logger.info(f"Login attempt {attempt + 1}/{max_retries}")
        try:
            driver.get(LOGIN_URL)

            username_field = wait_for_element(driver, By.ID, "txtname")
            password_field = wait_for_element(driver, By.ID, "txtPass")
            captcha_input = wait_for_element(driver, By.ID, "txt_captcha")

            if not all([username_field, password_field, captcha_input]):
                raise Exception("Login page elements not found or loaded.")

            username_field.clear()
            username_field.send_keys(username)
            password_field.clear()
            password_field.send_keys(password)

            captcha_base64 = get_captcha_image_base64(driver)
            captcha_solution = solve_captcha_api(api_key, captcha_base64)

            captcha_input.clear()
            captcha_input.send_keys(captcha_solution)

            logger.info("Submitting login form...")
            password_field.send_keys(Keys.ENTER)

            WebDriverWait(driver, DEFAULT_TIMEOUT*2).until(
                EC.url_contains(LOGIN_SUCCESS_URL_PART)
            )

            logger.info("Login successful! Current URL: %s", driver.current_url)
            dismiss_notifications(driver)
            return # Exit loop on success

        except (TimeoutException, NoSuchElementException) as e:
            logger.warning(f"Login attempt {attempt + 1} failed: Element not found or timeout. {e}")
            # Consider saving screenshots to a temporary location if needed
            # screenshot_path = f"login_error_attempt_{attempt+1}.png"
            # try:
            #     driver.save_screenshot(screenshot_path)
            #     logger.info(f"Screenshot saved to {screenshot_path}")
            # except Exception as screen_err:
            #     logger.error(f"Could not save screenshot: {screen_err}")
        except Exception as e:
            logger.error(f"Login attempt {attempt + 1} failed: {e}")
            logger.debug(traceback.format_exc())
            # screenshot_path = f"login_error_attempt_{attempt+1}.png"
            # try:
            #     driver.save_screenshot(screenshot_path)
            #     logger.info(f"Screenshot saved to {screenshot_path}")
            # except Exception as screen_err:
            #     logger.error(f"Could not save screenshot: {screen_err}")

            current_url = driver.current_url
            page_source = driver.page_source

            if "Login.aspx" in current_url:
                 if "Invalid username or password" in page_source:
                      logger.error("Login failed: Invalid username or password.")
                      raise Exception("Invalid credentials provided.")
                 elif "Invalid Security Code" in page_source or "captcha" in str(e).lower():
                      logger.warning("Login failed: Invalid CAPTCHA code. Retrying...")
                 else:
                      logger.warning("Login failed, likely CAPTCHA or other issue. Retrying...")
            else:
                 logger.error(f"Login failed, ended up on unexpected page: {current_url}")


        if attempt < max_retries - 1:
            logger.info("Waiting before next login attempt...")
            time.sleep(3 + attempt * 2)
        else:
            logger.error("Login failed after all retries.")
            raise Exception("Maximum login attempts reached.")


# --- Navigation ---
def navigate_to_page(driver, url, wait_element_selector):
    """Navigates to a specific URL and waits for a key element."""
    logger.info(f"Navigating to: {url}")
    try:
        driver.get(url)
        logger.info(f"Waiting for presence of element: {wait_element_selector}")
        WebDriverWait(driver, DEFAULT_TIMEOUT*1.5).until(
            EC.presence_of_element_located(wait_element_selector)
        )
        logger.info(f"Successfully navigated and key element '{wait_element_selector}' is present.")
        time.sleep(1.5)
        return True
    except TimeoutException:
        logger.error(f"Timeout waiting for element '{wait_element_selector}' after navigating to {url}")
        # driver.save_screenshot(f"navigation_timeout_{url.split('/')[-1]}.png")
        return False
    except Exception as e:
        logger.error(f"Error navigating to {url}: {e}")
        # driver.save_screenshot(f"navigation_error_{url.split('/')[-1]}.png")
        return False

# --- Data Scraping ---
def expand_course_panel(driver, course_element):
    """Expands a course panel if it's collapsed. Returns True if expanded or already open, False otherwise."""
    try:
        panel = course_element.find_element(By.CSS_SELECTOR, ".panel-collapse")
        is_expanded = "in" in panel.get_attribute("class")

        if not is_expanded:
            logger.debug("Panel is collapsed, attempting to expand.")
            toggle = None
            try:
                header = course_element.find_element(By.CSS_SELECTOR, ".panel-heading, .panel-title")
                toggle = header.find_element(By.CSS_SELECTOR, ".accordion-toggle, a[data-toggle='collapse']")
            except NoSuchElementException:
                 logger.debug("Could not find toggle in header, trying direct child.")
                 toggle = course_element.find_element(By.CSS_SELECTOR, ".accordion-toggle, a[data-toggle='collapse']")

            if not toggle:
                logger.warning("Could not find the expand toggle button for the course panel.")
                return False

            driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", toggle)
            time.sleep(0.5)

            logger.debug("Clicking the toggle.")
            if click_element_robustly(driver, toggle, timeout=10):
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: "in" in d.find_element(By.ID, panel.get_attribute("id")).get_attribute("class") if panel.get_attribute("id") else "in" in course_element.find_element(By.CSS_SELECTOR, ".panel-collapse").get_attribute("class")
                    )
                    logger.debug("Course panel successfully expanded.")
                    time.sleep(0.5)
                    return True
                except TimeoutException:
                    logger.warning("Panel did not expand (class 'in' not found) after clicking toggle.")
                    return False
                except NoSuchElementException:
                    logger.warning("Panel element disappeared after clicking toggle.")
                    return False
            else:
                logger.warning("Failed to click course panel toggle button.")
                return False
        else:
            logger.debug("Course panel already expanded.")
            return True

    except NoSuchElementException:
        logger.warning(f"Could not find expected panel structure (.panel-collapse) within course element.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error expanding course panel: {e}")
        logger.debug(traceback.format_exc())
        return False

def scrape_quizzes(driver):
    """Scrapes quiz data from the quizzes page."""
    logger.info("Starting quiz data extraction...")
    results = {
        "quizzes_with_results": [],
        "quizzes_without_results": [],
        "courses_processed": 0,
        "total_quizzes_found": 0,
        "courses_found_on_page": [],
        "quiz_courses_with_no_items": [],
        "quiz_courses_failed_expansion": []
    }
    courses = []
    try:
        logger.info("Waiting for course sections (section.course-item) to appear...")
        WebDriverWait(driver, DEFAULT_TIMEOUT * 1.5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "section.course-item"))
        )
        logger.info("Course sections found. Locating all course items...")
        courses = driver.find_elements(By.CSS_SELECTOR, "section.course-item")
        logger.info(f"Found {len(courses)} course sections on the page.")

    except TimeoutException:
        logger.error("Timeout waiting for any course sections (section.course-item) to appear on quizzes page.")
        # driver.save_screenshot("quiz_page_no_courses_found.png")
        return results

    if not courses:
        logger.warning("No course sections (section.course-item) found even after waiting.")
        # driver.save_screenshot("quiz_page_no_courses_found_empty_list.png")
        return results

    for index, course in enumerate(courses):
        course_name = f"Unknown Course {index+1}"
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", course)
            time.sleep(0.7)

            try:
                 name_element = WebDriverWait(course, 5).until(
                      EC.visibility_of_element_located((By.CSS_SELECTOR, "strong.course-name"))
                 )
                 course_name = safe_get_text(name_element) if name_element else f"Course Section {index+1}"
            except (TimeoutException, NoSuchElementException):
                 logger.warning(f"Could not find or make visible course name for section {index+1}. Using default.")

            results["courses_found_on_page"].append(course_name)
            logger.info(f"Processing quizzes for course: {course_name}")

            if not expand_course_panel(driver, course):
                logger.warning(f"Could not expand panel for {course_name} (Quizzes), skipping item extraction.")
                results["quiz_courses_failed_expansion"].append(course_name)
                continue

            results["courses_processed"] += 1

            quiz_articles = []
            try:
                quiz_articles = WebDriverWait(course, 5).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "article.quiz-item"))
                )
                logger.info(f"Found {len(quiz_articles)} quizzes in expanded section {course_name}")
            except TimeoutException:
                logger.info(f"No quiz items (article.quiz-item) found within expanded section {course_name}.")
                quiz_articles = []

            if not quiz_articles:
                 results["quiz_courses_with_no_items"].append(course_name)

            results["total_quizzes_found"] += len(quiz_articles)
            for quiz in quiz_articles:
                quiz_data = {"course": course_name, "type": "Quiz"}
                try:
                    name_elem = safe_find_element(quiz, By.CSS_SELECTOR, "a.quiz-name")
                    quiz_data["name"] = safe_get_text(name_elem) or "Unnamed Quiz"
                    status_elem = safe_find_element(quiz, By.CSS_SELECTOR, ".quiz-status")
                    raw_status = safe_get_text(status_elem)
                    if "Closed at:" in raw_status:
                        quiz_data["closed_at"] = raw_status.split("Closed at:")[1].strip()
                    elif "Will be opened at:" in raw_status:
                        quiz_data["closed_at"] = raw_status
                    elif "Opened at:" in raw_status:
                         quiz_data["closed_at"] = raw_status
                    else:
                        quiz_data["closed_at"] = raw_status if raw_status else "No Status/Date"
                    grade_elem = safe_find_element(quiz, By.CSS_SELECTOR, ".graded-status")
                    grade_text = safe_get_text(grade_elem)
                    quiz_data["grade"] = grade_text if grade_text and grade_text != "--" else "Not Graded"
                    attempts_elem = safe_find_element(quiz, By.CSS_SELECTOR, ".quiz-attempts")
                    quiz_data["attempts"] = safe_get_text(attempts_elem) if attempts_elem else "Attempts N/A"

                    if quiz_data["grade"] != "Not Graded" and "/" in quiz_data["grade"]:
                        results["quizzes_with_results"].append(quiz_data)
                    else:
                        results["quizzes_without_results"].append(quiz_data)
                    logger.debug(f"Extracted quiz: {quiz_data['name']} | Date: {quiz_data['closed_at']} | Grade: {quiz_data['grade']}")
                except StaleElementReferenceException:
                     logger.warning(f"Stale element reference while processing a quiz in {course_name}. Skipping this quiz.")
                     continue
                except Exception as e:
                    logger.warning(f"Error processing a quiz item in {course_name}: {e}. Data: {quiz_data}")
                    quiz_data.setdefault("name", "Error Processing Quiz")
                    quiz_data.setdefault("closed_at", "Unknown")
                    quiz_data.setdefault("grade", "Unknown")
                    quiz_data.setdefault("attempts", "Unknown")
                    results["quizzes_without_results"].append(quiz_data)

        except StaleElementReferenceException:
             logger.warning(f"Stale element reference encountered for course section {course_name} (Quizzes).")
             continue
        except Exception as e:
            logger.error(f"Failed to process course section '{course_name}' for quizzes: {e}")
            logger.debug(traceback.format_exc())
            if course_name not in results["quiz_courses_failed_expansion"]:
                 results["quiz_courses_failed_expansion"].append(course_name)
            continue

    logger.info(f"Finished quiz extraction. Processed {results['courses_processed']} courses, found {results['total_quizzes_found']} total quizzes.")
    return results

def scrape_assignments(driver):
    """Scrapes assignment data from the assignments page."""
    logger.info("Starting assignment data extraction...")
    results = {
        "assignments": [],
        "courses_processed": 0,
        "total_assignments_found": 0,
        "courses_found_on_page": [],
        "assignment_courses_with_no_items": [],
        "assignment_courses_failed_expansion": []
    }
    courses = []
    try:
        logger.info("Waiting for course sections (section.course-item) to appear...")
        WebDriverWait(driver, DEFAULT_TIMEOUT * 1.5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "section.course-item"))
        )
        logger.info("Course sections found. Locating all course items...")
        courses = driver.find_elements(By.CSS_SELECTOR, "section.course-item")
        logger.info(f"Found {len(courses)} course sections on the page.")

    except TimeoutException:
        logger.error("Timeout waiting for any course sections (section.course-item) to appear on assignments page.")
        # driver.save_screenshot("assignment_page_no_courses_found.png")
        return results

    if not courses:
        logger.warning("No course sections (section.course-item) found even after waiting.")
        # driver.save_screenshot("assignment_page_no_courses_found_empty_list.png")
        return results

    for index, course in enumerate(courses):
        course_name = f"Unknown Course {index+1}"
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", course)
            time.sleep(0.7)

            try:
                 name_element = WebDriverWait(course, 5).until(
                      EC.visibility_of_element_located((By.CSS_SELECTOR, "strong.course-name"))
                 )
                 course_name = safe_get_text(name_element) if name_element else f"Course Section {index+1}"
            except (TimeoutException, NoSuchElementException):
                 logger.warning(f"Could not find or make visible course name for section {index+1}. Using default.")

            results["courses_found_on_page"].append(course_name)
            logger.info(f"Processing assignments for course: {course_name}")

            if not expand_course_panel(driver, course):
                logger.warning(f"Could not expand panel for {course_name} (Assignments), skipping item extraction.")
                results["assignment_courses_failed_expansion"].append(course_name)
                continue

            results["courses_processed"] += 1

            assignment_articles = []
            try:
                assignment_articles = WebDriverWait(course, 5).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "article.assignment-item"))
                )
                logger.info(f"Found {len(assignment_articles)} assignments in expanded section {course_name}")
            except TimeoutException:
                logger.info(f"No assignment items (article.assignment-item) found within expanded section {course_name}.")
                assignment_articles = []

            if not assignment_articles:
                 results["assignment_courses_with_no_items"].append(course_name)

            results["total_assignments_found"] += len(assignment_articles)
            for assignment in assignment_articles:
                assignment_data = {"course": course_name, "type": "Assignment"}
                try:
                    name_elem = (safe_find_element(assignment, By.CSS_SELECTOR, ".assign-name") or
                                 safe_find_element(assignment, By.CSS_SELECTOR, "div.h5 a") or
                                 safe_find_element(assignment, By.CSS_SELECTOR, "a[href*='AssignmentDetails']"))
                    assignment_data["name"] = safe_get_text(name_elem) or "Unnamed Assignment"
                    submit_status_elem = safe_find_element(assignment, By.CSS_SELECTOR, ".submit-status")
                    if submit_status_elem:
                         assignment_data["submit_status"] = safe_get_text(submit_status_elem)
                    else:
                        status_divs = assignment.find_elements(By.CSS_SELECTOR, "div[class*='status']")
                        found_status = "Status Unknown"
                        for div in status_divs:
                            text = div.text.strip()
                            if "Submitted" in text or "Not Submitted" in text:
                                found_status = text
                                break
                        assignment_data["submit_status"] = found_status
                    date_elem = safe_find_element(assignment, By.CSS_SELECTOR, ".assign-status")
                    raw_date = ""
                    if date_elem:
                        try:
                             raw_date = driver.execute_script("return arguments[0].innerText;", date_elem).strip()
                        except Exception as script_err:
                             logger.warning(f"JS script for innerText failed on date element: {script_err}, using .text")
                             raw_date = safe_get_text(date_elem)
                    else:
                         potential_date_divs = assignment.find_elements(By.CSS_SELECTOR, "div small")
                         for div in potential_date_divs:
                              text = div.text.strip()
                              if re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b | \d{1,2}:\d{2} (AM|PM)| Will be closed', text, re.IGNORECASE):
                                   raw_date = text
                                   break
                    if "Closed at:" in raw_date:
                        assignment_data["closed_at"] = raw_date.split("Closed at:")[1].strip()
                    elif "Will be closed after:" in raw_date:
                        assignment_data["closed_at"] = raw_date
                    else:
                        assignment_data["closed_at"] = raw_date if raw_date else "No Deadline Info"
                    graded_elem = safe_find_element(assignment, By.CSS_SELECTOR, ".graded-status")
                    grade_text = safe_get_text(graded_elem)
                    assignment_data["grading_status"] = grade_text if grade_text and grade_text != "--" else "Not Graded Yet"

                    results["assignments"].append(assignment_data)
                    logger.debug(f"Extracted assignment: {assignment_data['name']} | Deadline: {assignment_data['closed_at']} | Submit: {assignment_data['submit_status']} | Grade: {assignment_data['grading_status']}")
                except StaleElementReferenceException:
                     logger.warning(f"Stale element reference while processing an assignment in {course_name}. Skipping this assignment.")
                     continue
                except Exception as e:
                    logger.warning(f"Error processing an assignment item in {course_name}: {e}. Data: {assignment_data}")
                    assignment_data.setdefault("name", "Error Processing Assignment")
                    assignment_data.setdefault("submit_status", "Unknown")
                    assignment_data.setdefault("closed_at", "Unknown")
                    assignment_data.setdefault("grading_status", "Unknown")
                    results["assignments"].append(assignment_data)

        except StaleElementReferenceException:
             logger.warning(f"Stale element reference encountered for course section {course_name} (Assignments).")
             continue
        except Exception as e:
            logger.error(f"Failed to process course section '{course_name}' for assignments: {e}")
            logger.debug(traceback.format_exc())
            if course_name not in results["assignment_courses_failed_expansion"]:
                 results["assignment_courses_failed_expansion"].append(course_name)
            continue

    logger.info(f"Finished assignment extraction. Processed {results['courses_processed']} courses, found {results['total_assignments_found']} total assignments.")
    return results


# --- Date Parsing ---
def parse_date(date_str):
    """Parse various absolute and relative date/time formats into datetime objects."""
    if not date_str or date_str in ["No Deadline Info", "No Status/Date", "N/A", "Unknown"]:
        return None

    date_str = date_str.replace('\n', ' ').strip()

    # 1. Handle Relative Dates
    relative_match = re.search(r"Will be closed after:.*?(\d+)\s*days?.*?(\d+)\s*hours?", date_str, re.IGNORECASE)
    if relative_match:
        try:
            days = int(relative_match.group(1))
            hours = int(relative_match.group(2))
            deadline = datetime.now() + timedelta(days=days, hours=hours)
            logger.debug(f"Parsed relative deadline: '{date_str}' -> {deadline}")
            return deadline
        except (ValueError, IndexError):
            logger.warning(f"Could not parse numbers from relative date: {date_str}")
            pass # Fall through

    # 2. Handle Absolute Dates
    date_str_cleaned = re.sub(r"^(Closed at:|Opened at:|Will be opened at:)\s*", "", date_str, flags=re.IGNORECASE).strip()
    date_str_cleaned = re.sub(r"(\b\w{3})\s+(\d),", r"\1 0\2,", date_str_cleaned) # Add leading zero to day

    formats = [
        "%b %d, %Y at %I:%M %p",
        "%B %d, %Y at %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
        "%a, %b %d, %Y %I:%M %p",
    ]

    for fmt in formats:
        try:
            parsed_date = datetime.strptime(date_str_cleaned, fmt)
            logger.debug(f"Parsed absolute deadline: '{date_str}' (cleaned: '{date_str_cleaned}') using format '{fmt}' -> {parsed_date}")
            return parsed_date
        except ValueError:
            continue

    logger.warning(f"Failed to parse date string: '{date_str}' (cleaned: '{date_str_cleaned}') with any known format.")
    return None

# --- Deadline Checking & Reporting ---
def check_upcoming_deadlines(data, days_threshold=DEADLINE_THRESHOLD_DAYS):
    """Checks combined data for upcoming deadlines within the threshold."""
    upcoming = []
    now = datetime.now()
    logger.info(f"Checking for deadlines upcoming within {days_threshold} days from {now.strftime('%Y-%m-%d %H:%M')}")

    all_tasks = data.get("assignments", {}).get("assignments", []) + \
                data.get("quizzes", {}).get("quizzes_with_results", []) + \
                data.get("quizzes", {}).get("quizzes_without_results", [])

    if not all_tasks:
        logger.info("No assignments or quizzes found in the data to check for deadlines.")
        return []

    for task in all_tasks:
        close_date_str = task.get("closed_at") or task.get("status")
        if not close_date_str:
            logger.debug(f"Skipping task '{task.get('name')}' due to missing date field.")
            continue

        deadline = parse_date(close_date_str)

        if deadline:
            if deadline > now:
                time_difference = deadline - now
                days_left = time_difference.days
                if 0 <= days_left <= days_threshold:
                    upcoming.append({
                        "course": task.get("course", "N/A"),
                        "name": task.get("name", "Unnamed Task"),
                        "due_date_str": close_date_str,
                        "due_date_obj": deadline,
                        "days_left": days_left,
                        "type": task.get("type", "Task")
                    })

    upcoming.sort(key=lambda x: x["due_date_obj"])
    logger.info(f"Found {len(upcoming)} upcoming deadlines within {days_threshold} days.")
    return upcoming

def send_deadline_alerts(upcoming_tasks, webhook_url):
    """Sends a Discord notification with upcoming deadlines using embeds."""
    if not webhook_url or "discord.com/api/webhooks" not in webhook_url:
        logger.error("Invalid or missing Discord webhook URL provided.")
        return # Don't send if no valid webhook

    max_embeds = 10
    chunks = [upcoming_tasks[i:i + max_embeds] for i in range(0, len(upcoming_tasks), max_embeds)]

    if not chunks:
        message = {
            "content": "✅ **All Clear!**",
            "embeds": [{
                "title": "No Upcoming Deadlines",
                "description": f"No assignments or quizzes found due in the next {DEADLINE_THRESHOLD_DAYS} days.",
                "color": 3066993,
                "footer": {"text": f"Checked on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
            }]
        }
        try:
            response = requests.post(webhook_url, json=message, headers={"Content-Type": "application/json"}, timeout=10)
            response.raise_for_status()
            logger.info(f"Sent 'No Deadlines' notification to Discord ({response.status_code}).")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send 'No Deadlines' Discord notification: {e}")
        return

    for i, chunk in enumerate(chunks):
        embeds = []
        for task in chunk:
            if task["days_left"] <= 1: color = 15158332 # Red
            elif task["days_left"] <= 3: color = 15105570 # Orange
            else: color = 16776960 # Yellow

            if task["days_left"] == 0: days_left_str = "**Due Today!**"
            elif task["days_left"] == 1: days_left_str = "**Due Tomorrow!**"
            else: days_left_str = f"{task['days_left']} days"

            embed = {
                "title": f"{task['type']}: {task.get('name', 'Unnamed Task')}",
                "color": color,
                "fields": [
                    {"name": "Course", "value": task.get('course', 'N/A'), "inline": True},
                    {"name": "Due Date", "value": task['due_date_obj'].strftime("%a, %b %d, %Y %I:%M %p"), "inline": True},
                    {"name": "Days Left", "value": days_left_str, "inline": True}
                ],
            }
            embeds.append(embed)

        message = {
            "content": f"🔔 **Upcoming Deadlines Alert!** (Part {i+1}/{len(chunks)}) - Checked: {datetime.now().strftime('%H:%M')}",
            "embeds": embeds
        }

        try:
            logger.info(f"Sending deadline chunk {i+1} to Discord...")
            response = requests.post(
                webhook_url,
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            logger.info(f"Discord response status: {response.status_code}")
            response.raise_for_status()
            logger.info(f"Discord alert chunk {i+1} sent successfully.")
            if len(chunks) > 1 and i < len(chunks) - 1:
                time.sleep(1.2)

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Discord alert chunk {i+1}: {e}")
        except Exception as e:
             logger.error(f"An unexpected error occurred sending Discord alert chunk {i+1}: {e}")


def generate_data_quality_report(data):
    """Generates a simple quality report for the scraped data."""
    report = {"issues": []}
    assignments = data.get("assignments", {}).get("assignments", [])
    quizzes_w_results = data.get("quizzes", {}).get("quizzes_with_results", [])
    quizzes_wo_results = data.get("quizzes", {}).get("quizzes_without_results", [])
    all_quizzes = quizzes_w_results + quizzes_wo_results

    total_assignments = len(assignments)
    total_quizzes = len(all_quizzes)

    assign_missing_names = sum(1 for a in assignments if not a.get("name") or "Unnamed" in a.get("name", "") or "Error Processing" in a.get("name", ""))
    quiz_missing_names = sum(1 for q in all_quizzes if not q.get("name") or "Unnamed" in q.get("name", "") or "Error Processing" in q.get("name", ""))
    if assign_missing_names > 0: report["issues"].append(f"Assignments missing/default/error names: {assign_missing_names}/{total_assignments}")
    if quiz_missing_names > 0: report["issues"].append(f"Quizzes missing/default/error names: {quiz_missing_names}/{total_quizzes}")

    assign_bad_dates = sum(1 for a in assignments if a.get("closed_at") and a.get("closed_at") not in ["No Deadline Info", "N/A", "Unknown"] and parse_date(a.get("closed_at")) is None)
    quiz_bad_dates = sum(1 for q in all_quizzes if q.get("closed_at") and q.get("closed_at") not in ["No Status/Date", "N/A", "Unknown"] and parse_date(q.get("closed_at")) is None)
    if assign_bad_dates > 0: report["issues"].append(f"Assignments with unparseable dates: {assign_bad_dates}/{total_assignments}")
    if quiz_bad_dates > 0: report["issues"].append(f"Quizzes with unparseable dates: {quiz_bad_dates}/{total_quizzes}")

    assign_missing_submit = sum(1 for a in assignments if not a.get("submit_status") or "Unknown" in a.get("submit_status", ""))
    if assign_missing_submit > 0: report["issues"].append(f"Assignments missing/unknown submission status: {assign_missing_submit}/{total_assignments}")

    assign_missing_grade = sum(1 for a in assignments if not a.get("grading_status") or "Unknown" in a.get("grading_status", ""))
    quiz_missing_grade = sum(1 for q in all_quizzes if not q.get("grade") or "Unknown" in q.get("grade", ""))
    if assign_missing_grade > 0: report["issues"].append(f"Assignments missing/unknown grading status: {assign_missing_grade}/{total_assignments}")
    if quiz_missing_grade > 0: report["issues"].append(f"Quizzes missing/unknown grade: {quiz_missing_grade}/{total_quizzes}")

    if not report["issues"]: report["summary"] = "Data quality appears good."
    else: report["summary"] = f"Potential data quality issues found ({len(report['issues'])} categories)."

    logger.info(f"Data Quality Report: {report['summary']} Issues: {'; '.join(report['issues']) if report['issues'] else 'None'}")
    return report


# --- Main Scraper Function ---
def run_dulms_scraper(log_queue: queue.Queue, username: str, password: str, captcha_api_key: str, discord_webhook: str | None):
    """
    Main function to orchestrate the scraping process.
    Accepts credentials and a queue for logging.
    Returns the scraped data dictionary or raises an exception.
    """
    # Setup logging handler for this specific run to use the provided queue
    queue_handler = logging.handlers.QueueHandler(log_queue)
    # Use a specific format for logs going into the queue if desired, or keep it simple
    # formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    # queue_handler.setFormatter(formatter)

    # Add the handler to the module-level logger for this run
    logger.addHandler(queue_handler)
    # Set level for this handler if different from logger's base level needed
    # queue_handler.setLevel(logging.INFO)

    start_time = time.time()
    logger.info("--- Starting DULMS Scraper Task ---")
    driver = None
    scraped_data = {
        "quizzes": {"quizzes_with_results": [], "quizzes_without_results": [], "courses_processed": 0, "total_quizzes_found": 0, "courses_found_on_page": [], "quiz_courses_with_no_items": [], "quiz_courses_failed_expansion": []},
        "assignments": {"assignments": [], "courses_processed": 0, "total_assignments_found": 0, "courses_found_on_page": [], "assignment_courses_with_no_items": [], "assignment_courses_failed_expansion": []}
    }

    try:
        # Initialize driver (ensure headless is True)
        driver = initialize_driver(headless=True)

        # Login using provided credentials
        login(driver, username, password, captcha_api_key) # MAX_LOGIN_RETRIES constant is used

        # Navigate and Scrape Quizzes
        if navigate_to_page(driver, QUIZZES_URL, (By.CSS_SELECTOR, "section.course-item")):
            scraped_data["quizzes"] = scrape_quizzes(driver)
        else:
             logger.error("Failed to navigate to Quizzes page or find initial course items. Skipping quiz scraping.")
             # Consider if partial results should be returned or if this is a critical failure
             # raise Exception("Failed to navigate to Quizzes page.")

        # Navigate and Scrape Assignments
        if navigate_to_page(driver, ASSIGNMENTS_URL, (By.CSS_SELECTOR, "section.course-item")):
            scraped_data["assignments"] = scrape_assignments(driver)
        else:
             logger.error("Failed to navigate to Assignments page or find initial course items. Skipping assignment scraping.")
             # Consider if partial results should be returned or if this is a critical failure
             # raise Exception("Failed to navigate to Assignments page.")

        # NOTE: Saving to file is removed, caller (FastAPI) will handle the result
        # logger.info(f"Scraped data obtained.")

        # Check deadlines and send alerts (if webhook provided)
        if discord_webhook:
             upcoming_deadlines = check_upcoming_deadlines(scraped_data) # DEADLINE_THRESHOLD_DAYS constant used
             send_deadline_alerts(upcoming_deadlines, discord_webhook)
        else:
             logger.info("No Discord webhook provided, skipping deadline alerts.")

        # Generate and log quality report (logs via queue handler)
        generate_data_quality_report(scraped_data)

        logger.info("Scraper task completed successfully.")
        return scraped_data

    except Exception as e:
        # Log the critical error via the queue
        logger.critical(f"An critical error occurred during scraper execution: {e}")
        logger.critical(traceback.format_exc())
        # Raise the exception so the FastAPI background task handler knows it failed
        raise # Re-raise the exception

    finally:
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed successfully.")
            except Exception as e:
                logger.error(f"Error quitting WebDriver: {e}")
        end_time = time.time()
        logger.info(f"--- DULMS Scraper Task finished in {end_time - start_time:.2f} seconds ---")
        # Remove the handler specific to this run to prevent duplicate logs if function is called again
        logger.removeHandler(queue_handler)

# Note: No if __name__ == "__main__": block here, this module is intended to be imported.