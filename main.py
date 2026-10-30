"""Employee Attendance & Analytics API."""
import os, re, calendar
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
PRESENCE = ("PRESENT", "WFH", "ON_DUTY")
STATUSES = (*PRESENCE, "ABSENT", "LEAVE")

@asynccontextmanager
async def lifespan(app):
    try:
        db.command("ping")
        db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
        db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
        db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING)], name="attendance_date_status")
        db.attendance_logs.create_index([("date", ASCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
        db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING)], name="attendance_employee_date")
    except Exception:
        pass
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

# ... rest of the original file remains unchanged ...

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level=os.getenv("LOG_LEVEL", "info"))
