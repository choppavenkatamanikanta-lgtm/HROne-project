EMPLOYEE ATTENDANCE &
ANALYTICS API
Project README
1. Project Overview
A RESTful API built with Python, FastAPI, and MongoDB to manage employee records, track daily attendance, calculate working hours, and generate employee and department analytics.
The API supports employee registration, punch-in and punch-out operations, attendance corrections, monthly reports, department summaries, late-arrival leaderboards, and attendance trends. FastAPI and Pydantic provide API development and request validation, while MongoDB stores and aggregates attendance data.
2. Features
Employee Management: Create employee records and retrieve paginated lists with optional department filtering.
Attendance Tracking: Record employee punch-in and punch-out timestamps.
Work Hours Calculation: Calculate working hours, late minutes, overtime, and half-day indicators.
Attendance Regularization: Correct attendance status and timestamps while keeping a history of changes.
Attendance Filtering: Filter records by employee, date range, and status.
Monthly Employee Analytics: Report working days, attendance percentage, leave days, late arrivals, and overtime.
Department Analytics: Summarize headcount, attendance, and average work hours.
Late-Arrival Leaderboard: Rank employees by accumulated late minutes.
Attendance Trend Analysis: Analyze department attendance rates and seven-day moving averages.
Health Check: Verify MongoDB connectivity.
Query Explain: Inspect MongoDB query execution statistics for supported analytics endpoints.
3. Technologies Used
Python
FastAPI
MongoDB
PyMongo
Pydantic
Uvicorn
python-dotenv
Git and GitHub
4. Project Structure
employee-attendance-analytics-api/
├── main.py
├── .env
├── .gitignore
└── README.md
This is an example structure for a project with main.py in the repository root. Adjust the paths if your project uses an app/ package or includes additional files.
5. Prerequisites
Python 3.10 or later is recommended.
MongoDB Community Server or a MongoDB Atlas database.
Git.
Visual Studio Code (optional).
Use a MongoDB version that supports the aggregation features used by the analytics endpoints; MongoDB 5.0 or later is recommended.
6. Installation and Setup
Step 1: Clone the repository
git clone https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
cd YOUR-REPOSITORY
Replace the example URL with your actual GitHub repository URL.
Step 2: Create a virtual environment
python -m venv .venv
Activate on Windows:
.venv\Scripts\activate
Step 3: Install dependencies
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" pymongo python-dotenv
If your project includes requirements.txt, install dependencies with:
python -m pip install -r requirements.txt
Step 4: Configure environment variables
Create a .env file in the project root:
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
PORT=8000
LOG_LEVEL=info
For MongoDB Atlas, replace the local URI with your Atlas connection string. Keep credentials private and never commit .env.
Step 5: Start the API
If main.py is in the project root and contains the FastAPI app:
uvicorn main:app --reload --port 8000
If the file is app/main.py, use:
uvicorn app.main:app --reload --port 8000
Make sure MongoDB is running and accessible before testing database-dependent endpoints.
7. API Endpoints
Method	Endpoint	Description
GET	/health	Check database connectivity
POST	/employees	Create an employee
GET	/employees	List employees
POST	/attendance/punch-in	Record punch-in
POST	/attendance/punch-out	Record punch-out
GET	/attendance	Retrieve attendance records
PATCH	/attendance/{emp_code}/{date_str}	Regularize attendance
GET	/analytics/employees/{emp_code}/monthly	Monthly employee analytics
GET	/analytics/departments/summary	Department summaries
GET	/analytics/leaderboard/late	Late-arrival rankings
GET	/analytics/departments/{department}/trend	Department attendance trends
GET	/admin/explain/{endpoint}	Inspect supported query execution plans
8. Testing the API
When the server is running, open the interactive API documentation:
Swagger UI: http://127.0.0.1:8000/docs
ReDoc: http://127.0.0.1:8000/redoc
Example: Create an employee
Request: POST /employees
{
  "emp_code": "EMP1001",
  "name": "Rahul Kumar",
  "email": "rahul@example.com",
  "department": "Engineering",
  "shift_start": "09:30",
  "shift_end": "18:30",
  "joined_on": "2026-01-15"
}
The response contains the employee details and a creation timestamp.
Example: Punch in
Request: POST /attendance/punch-in
{
  "emp_code": "EMP1001",
  "status": "PRESENT"
}
When punched_at is omitted, the API records the current time. Alternatively, punched_at can be supplied as an epoch timestamp in milliseconds.
Example: Monthly analytics
GET /analytics/employees/EMP1001/monthly?month=2026-01
Returns monthly metrics including working days, present days, leave days, late arrivals, overtime, and attendance percentage.
9. Validation and Error Handling
404 Not Found — employee or attendance record not found.
409 Conflict — duplicate employee code, duplicate attendance entry, or conflicting updates.
422 Unprocessable Entity — invalid input or business-rule violations.
503 Service Unavailable — MongoDB is unavailable during the health check.
10. Learning Outcomes
REST API development using FastAPI.
MongoDB integration using PyMongo.
Request validation using Pydantic.
CRUD operations and pagination.
MongoDB indexes and aggregation pipelines.
Date-time handling and attendance calculations.
API testing and interactive documentation.
Version control using Git and GitHub.
11. Future Enhancements
Add authentication and role-based authorization.
Build a frontend dashboard for HR teams.
Add automated tests using Pytest.
Integrate CI/CD workflows.
Add export functionality for attendance reports.
Deploy the API to a cloud platform.
12. Author
Choppa Venkata Manikanta
GitHub: https://github.com/YOUR-USERNAME
13. License
This project is intended for educational and portfolio purposes. Add a LICENSE file if you intend to distribute it under a specific open-source license.
