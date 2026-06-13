# Spec: Add GET /api/status Endpoint to jobs/dashboard/app.py

1. Open the file ~/watson/jobs/dashboard/app.py

2. Locate the existing Flask app instance (do not create a new one)

3. Add a new route decorator @app.route('/api/status', methods=['GET']) before a new function named status()

4. Inside the status() function, import datetime at module level if not already present, get the current time using datetime.datetime.now(), and return a JSON response using Flask's jsonify() function with the current time as an ISO format string in a field named "current_time"

5. The response should follow this structure: {"current_time": "YYYY-MM-DDTHH:MM:SS.ffffff"} where the datetime is formatted using .isoformat()

6. Ensure jsonify is imported at module level from flask if not already imported

7. Test the endpoint is accessible at GET http://localhost:5000/api/status (or appropriate port) and returns valid JSON with a current_time field

8. Run: git add -A && git commit -m 'Add GET /api/status endpoint with current time' && git push origin main