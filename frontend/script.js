document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('scraper-form');
    const runButton = document.getElementById('run-button');
    const statusMessage = document.getElementById('status-message');
    const logOutput = document.getElementById('log-output');
    const resultsOutput = document.getElementById('results-output');

    let eventSource = null; // To keep track of the SSE connection

    form.addEventListener('submit', async (event) => {
        event.preventDefault(); // Prevent default form submission

        // Clear previous state
        clearOutputs();
        setStatus('running', 'Initiating scraper...');
        runButton.disabled = true;
        closeEventSource(); // Close any existing connection

        // Get form data
        const formData = new FormData(form);
        const data = {
            username: formData.get('username'),
            password: formData.get('password'),
            captcha_api_key: formData.get('captcha_api_key'),
            discord_webhook: formData.get('discord_webhook') || null // Send null if empty
        };

        // Basic validation
        if (!data.username || !data.password || !data.captcha_api_key) {
            setStatus('error', 'Please fill in all required fields.');
            runButton.disabled = false;
            return;
        }

        try {
            // Call the backend API to start the scraper
            const response = await fetch('/api/run-scraper', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(data)
            });

            if (!response.ok) {
                let errorMsg = `Failed to start scraper: ${response.statusText}`;
                try {
                    const errorData = await response.json();
                    errorMsg = `Failed to start scraper: ${errorData.detail || response.statusText}`;
                } catch (e) { /* Ignore JSON parsing error */ }
                throw new Error(errorMsg);
            }

            const result = await response.json();
            const taskId = result.task_id;

            if (!taskId) {
                throw new Error('Backend did not return a task ID.');
            }

            setStatus('running', `Scraper process started (Task ID: ${taskId}). Waiting for logs...`);
            // Connect to the SSE endpoint
            connectEventSource(taskId);

        } catch (error) {
            console.error('Error starting scraper:', error);
            setStatus('error', `Error: ${error.message}`);
            runButton.disabled = false;
        }
    });

    function connectEventSource(taskId) {
        const url = `/api/stream-logs/${taskId}`;
        eventSource = new EventSource(url);
        console.log(`Connecting to SSE at ${url}`);
        logOutput.innerHTML = '<code>Connecting to log stream...</code>'; // Initial message

        eventSource.onopen = () => {
            console.log('SSE connection opened.');
            logOutput.innerHTML += '\n<code>Log stream connected.</code>';
        };

        eventSource.onmessage = (event) => {
            try {
                const messageData = JSON.parse(event.data);
                // console.log('Received SSE:', messageData);

                switch (messageData.type) {
                    case 'log':
                        appendLog(messageData.data);
                        break;
                    case 'status':
                        handleStatusUpdate(messageData);
                        break;
                    case 'results':
                        displayResults(messageData.data);
                        break;
                    case 'error': // Handle explicit error messages from SSE stream itself
                        console.error('SSE stream error message:', messageData.message);
                        setStatus('error', `Stream Error: ${messageData.message}`);
                        closeEventSource();
                        runButton.disabled = false;
                        break;
                    default:
                        console.warn('Unknown SSE message type:', messageData.type);
                }
            } catch (error) {
                console.error('Error processing SSE message:', error, 'Data:', event.data);
                // Avoid flooding with errors for non-JSON messages if any
                appendLog(`--- Error parsing message: ${event.data} ---`);
            }
        };

        eventSource.onerror = (error) => {
            console.error('SSE connection error:', error);
            setStatus('error', 'Log stream connection failed or was closed unexpectedly.');
            closeEventSource();
            runButton.disabled = false;
        };
    }

    function handleStatusUpdate(statusData) {
        if (statusData.data === 'completed') {
            setStatus('completed', 'Scraping task completed successfully.');
            closeEventSource();
            runButton.disabled = false;
        } else if (statusData.data === 'error') {
            const errorMessage = statusData.message || 'Unknown error during scraping.';
            setStatus('error', `Scraping task failed: ${errorMessage}`);
            closeEventSource();
            runButton.disabled = false;
        } else {
             // Could handle other intermediate statuses if needed
             console.log("Received status update:", statusData.data);
        }
    }

    function appendLog(logMessage) {
        // Ensure logOutput exists
        if (logOutput.innerHTML === '<code>Connecting to log stream...</code>' || logOutput.innerHTML === '<code>Log stream connected.</code>') {
            logOutput.innerHTML = ''; // Clear initial messages
        }
        const codeElement = document.createElement('code');
        codeElement.textContent = logMessage;
        logOutput.appendChild(codeElement);
        logOutput.scrollTop = logOutput.scrollHeight; // Auto-scroll to bottom
    }

    function displayResults(results) {
        resultsOutput.innerHTML = ''; // Clear previous results

    if (!results || typeof results !== 'object' || (!results.quizzes && !results.assignments)) {
        resultsOutput.innerHTML = '<p>No results data received or data is empty.</p>';
        return;
    }

    try {
        // --- Process Data: Group by Course ---
        const coursesData = {};
        const allAssignments = results.assignments?.assignments || [];
        const allQuizzes = [
            ...(results.quizzes?.quizzes_with_results || []),
            ...(results.quizzes?.quizzes_without_results || [])
        ];

        // Collect all unique course names
        const courseNames = new Set([
            ...allAssignments.map(a => a.course),
            ...allQuizzes.map(q => q.course),
            ...(results.quizzes?.courses_found_on_page || []), // Include courses even if they had no items
            ...(results.assignments?.courses_found_on_page || [])
        ]);

        // Initialize structure
        courseNames.forEach(name => {
            if (name && name !== "Unknown Course") { // Filter out potential null/empty names
                 coursesData[name] = { assignments: [], quizzes: [] };
            }
        });

        // Populate with assignments and quizzes
        allAssignments.forEach(assignment => {
            if (coursesData[assignment.course]) {
                coursesData[assignment.course].assignments.push(assignment);
            } else {
                 console.warn(`Assignment course "${assignment.course}" not found in initial set.`);
                 // Optionally handle assignments for courses not initially found (e.g., create the entry)
                 // if (!coursesData[assignment.course]) coursesData[assignment.course] = { assignments: [], quizzes: [] };
                 // coursesData[assignment.course].assignments.push(assignment);
            }
        });
        allQuizzes.forEach(quiz => {
             if (coursesData[quiz.course]) {
                coursesData[quiz.course].quizzes.push(quiz);
            } else {
                console.warn(`Quiz course "${quiz.course}" not found in initial set.`);
                 // Optionally handle quizzes for courses not initially found
                 // if (!coursesData[quiz.course]) coursesData[quiz.course] = { assignments: [], quizzes: [] };
                 // coursesData[quiz.course].quizzes.push(quiz);
            }
        });

         // --- Generate HTML ---
        const sortedCourseNames = Object.keys(coursesData).sort();

        if (sortedCourseNames.length === 0) {
            resultsOutput.innerHTML = '<p>No courses found in the results.</p>';
            return;
        }

        sortedCourseNames.forEach(courseName => {
            const courseInfo = coursesData[courseName];
            const courseCard = document.createElement('div');
            courseCard.className = 'course-card';

            const courseTitle = document.createElement('h3');
            courseTitle.textContent = courseName;
            courseCard.appendChild(courseTitle);

            // Assignments Section (Collapsible)
            if (courseInfo.assignments.length > 0) {
                const assignmentsDetails = document.createElement('details');
                assignmentsDetails.className = 'course-section';
                const assignmentsSummary = document.createElement('summary');
                assignmentsSummary.textContent = `Assignments (${courseInfo.assignments.length})`;
                assignmentsDetails.appendChild(assignmentsSummary);
                appendList(assignmentsDetails, '', courseInfo.assignments, formatAssignment); // No title needed for list here
                courseCard.appendChild(assignmentsDetails);
            } else {
                const p = document.createElement('p');
                p.className = 'no-items';
                p.textContent = 'No assignments found for this course.';
                courseCard.appendChild(p);
            }


            // Quizzes Section (Collapsible)
             if (courseInfo.quizzes.length > 0) {
                const quizzesDetails = document.createElement('details');
                quizzesDetails.className = 'course-section';
                const quizzesSummary = document.createElement('summary');
                quizzesSummary.textContent = `Quizzes (${courseInfo.quizzes.length})`;
                quizzesDetails.appendChild(quizzesSummary);
                appendList(quizzesDetails, '', courseInfo.quizzes, formatQuiz); // No title needed for list here
                courseCard.appendChild(quizzesDetails);
            } else {
                 const p = document.createElement('p');
                 p.className = 'no-items';
                 p.textContent = 'No quizzes found for this course.';
                 courseCard.appendChild(p);
            }


            resultsOutput.appendChild(courseCard);
        });

         // Optionally display summary stats or courses with issues at the end
         appendErrorSummary(resultsOutput, results);


    } catch (error) {
        console.error("Error processing or displaying results:", error);
        resultsOutput.innerHTML = `<p style="color: red;">Error displaying results: ${error.message}</p>`;
        }
    }

    function createSection(title) {
        const sectionDiv = document.createElement('div'); // Create the container div
        // Helper to create a styled section title
        const heading = document.createElement('h4'); // Use h4 for sub-sections
        heading.textContent = title;
        heading.style.marginTop = '10px'; // Add some space
        sectionDiv.appendChild(heading); // Append heading to the new div
        return sectionDiv; // Return the container div
    }

    function appendStat(section, label, value) {
        if (value !== undefined && value !== null) {
            const p = document.createElement('p');
            p.innerHTML = `<strong>${label}:</strong> ${value}`;
            section.appendChild(p);
        }
    }

     function appendList(section, title, items, formatter) {
        // If title is provided (used for the list within details), add it
        if (title) {
             const listTitle = document.createElement('h5'); // Use h5 for list titles
             listTitle.textContent = title;
             section.appendChild(listTitle);
        }
        if (items && items.length > 0) {
            const ul = document.createElement('ul');
            items.forEach(item => {
                const li = document.createElement('li');
                li.innerHTML = formatter(item); // Use existing formatters
                ul.appendChild(li);
            });
            section.appendChild(ul);
        } else if (items) {
             // Don't add 'None found' here as it's handled per course section
        } else {
             // Handle case where items is null/undefined if necessary
            const p = document.createElement('p');
            p.textContent = `No data available for ${title || 'this list'}.`;
            section.appendChild(p);
        }
    }

    function appendErrorSummary(section, results) {
        const issues = [];
        if (results.quizzes?.quiz_courses_failed_expansion?.length > 0) {
            issues.push(`Quiz Courses Failed Expansion: ${results.quizzes.quiz_courses_failed_expansion.join(', ')}`);
        }
         if (results.assignments?.assignment_courses_failed_expansion?.length > 0) {
            issues.push(`Assignment Courses Failed Expansion: ${results.assignments.assignment_courses_failed_expansion.join(', ')}`);
        }
        // Add more summary points if needed (e.g., from data quality report if passed)

        if (issues.length > 0) {
             const summarySection = createSection('Processing Summary / Issues');
             summarySection.style.marginTop = '30px';
             summarySection.style.borderTop = '1px solid #ccc';
             summarySection.style.paddingTop = '15px';
             const ul = document.createElement('ul');
             issues.forEach(issue => {
                  const li = document.createElement('li');
                  li.style.color = '#e74c3c'; // Reddish color for issues
                  li.textContent = issue;
                  ul.appendChild(li);
             });
             summarySection.appendChild(ul);
             section.appendChild(summarySection);
        }
    }

    function appendListRaw(section, title, items) {
        if (items && items.length > 0) {
            const listTitle = document.createElement('h5'); // Use h5 for these lists too
            listTitle.textContent = title;
            section.appendChild(listTitle);
            const ul = document.createElement('ul');
            items.forEach(item => {
                const rawLi = document.createElement('li'); // Renamed variable and removed duplicate line below
                rawLi.textContent = item;
                rawLi.style.fontStyle = 'italic'; // Differentiate raw list
                ul.appendChild(rawLi);
            });
            section.appendChild(ul);
        }
    }


    function formatQuiz(quiz) {
        return `
            // No Course needed as it's under the course card now
            <strong>Name:</strong> ${quiz.name || 'N/A'}<br>
            <small><strong>Status/Date:</strong> ${quiz.closed_at || 'N/A'} | <strong>Grade:</strong> ${quiz.grade || 'N/A'} | <strong>Attempts:</strong> ${quiz.attempts || 'N/A'}</small>
        `;
    }

    function formatAssignment(assignment) {
        return `
             // No Course needed as it's under the course card now
            <strong>Name:</strong> ${assignment.name || 'N/A'}<br>
            <small><strong>Deadline:</strong> ${assignment.closed_at || 'N/A'} | <strong>Submit Status:</strong> ${assignment.submit_status || 'N/A'} | <strong>Grading Status:</strong> ${assignment.grading_status || 'N/A'}</small>
        `;
    }


    function setStatus(type, message) {
        statusMessage.className = `status ${type}`; // Add class for styling
        statusMessage.textContent = message;
        statusMessage.style.display = 'block'; // Make sure it's visible
    }

    function clearOutputs() {
        statusMessage.textContent = '';
        statusMessage.style.display = 'none'; // Hide it initially
        statusMessage.className = 'status'; // Reset classes
        logOutput.innerHTML = '';
        resultsOutput.innerHTML = '';
    }

    function closeEventSource() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
            console.log('SSE connection closed.');
        }
    }

    // Initial state
    clearOutputs();
});