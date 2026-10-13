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
        # Startup should not hide a real MongoDB error; health reports readiness.
        pass
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)

def now_utc():
    return datetime.now(UTC).replace(microsecond=0)

def epoch_dt(value):
    if value is None: return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100_000_000_000 <= value <= 4_102_444_800_000:
        raise ValueError("timestamp must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)

def dt_utc(value):
    if value is None: return None
    if value.tzinfo is None: return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

def millis(value):
    if value is None: return None
    return int(dt_utc(value).timestamp() * 1000)

def serial(value):
    if isinstance(value, ObjectId): return str(value)
    if isinstance(value, datetime): return millis(value)
    if isinstance(value, list): return [serial(v) for v in value]
    if isinstance(value, dict): return {k: serial(v) for k, v in value.items() if k != "_id"}
    return value

def round_half_up(value, places=2):
    if value is None: return None
    return float(Decimal(str(value)).quantize(Decimal("1." + "0"*places), rounding=ROUND_HALF_UP))

def valid_date(s):
    try: return date.fromisoformat(s)
    except Exception: raise HTTPException(422, "date must be YYYY-MM-DD")

def valid_month(s):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s or ""):
        raise HTTPException(422, "month must be YYYY-MM")
    y, m = map(int, s.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y,m)[1])

def shift_dt(day, shift, overnight_next=False):
    h, m = map(int, shift.split(":"))
    return datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m, tzinfo=IST).astimezone(UTC)

def attendance_day(ts, emp):
    local = dt_utc(ts).astimezone(IST)
    sh, eh = emp["shift_start"], emp["shift_end"]
    if eh <= sh and local.strftime("%H:%M") < eh:
        return (local.date() - timedelta(days=1)).isoformat()
    return local.date().isoformat()

def compute_late_minutes(punch_in, shift_start):
    local = dt_utc(punch_in).astimezone(IST)
    h, m = map(int, shift_start.split(":"))
    start = local.replace(hour=h, minute=m, second=0, microsecond=0)
    mins = int((local-start).total_seconds() // 60)
    return mins if (local-start).total_seconds() > 600 else 0

def compute_work_hours(punch_in, punch_out):
    return round_half_up((dt_utc(punch_out)-dt_utc(punch_in)).total_seconds()/3600, 2)

def compute_overtime(punch_out, shift_end, date_str, shift_start):
    day = date.fromisoformat(date_str)
    end_day = day + timedelta(days=1) if shift_end <= shift_start else day
    end = shift_dt(end_day, shift_end)
    minutes = int((dt_utc(punch_out)-end).total_seconds() // 60)
    return minutes if minutes >= 30 else 0

def enrich_record(doc):
    out = serial(doc)
    out.setdefault("work_hours", None); out.setdefault("late_minutes", 0)
    out.setdefault("overtime_minutes", 0); out.setdefault("half_day", False)
    out.setdefault("history", [])
    return out

def attendance_pipeline(match=None):
    p = [{"$match": match or {}}, {"$sort": {"date": -1, "emp_code": 1}}]
    return p

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str
    @field_validator("joined_on")
    @classmethod
    def date_valid(cls,v): date.fromisoformat(v); return v
    @field_validator("shift_end")
    @classmethod
    def shifts_differ(cls,v,info):
        if info.data.get("shift_start") == v: raise ValueError("shift_start must differ from shift_end")
        return v

class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT","WFH","ON_DUTY"] = "PRESENT"
    @field_validator("punched_at")
    @classmethod
    def check_ts(cls,v): 
        if v is not None: epoch_dt(v)
        return v

class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emp_code: str
    punched_at: Optional[int] = None
    @field_validator("punched_at")
    @classmethod
    def check_ts(cls,v):
        if v is not None: epoch_dt(v)
        return v

class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    @field_validator("punch_in","punch_out")
    @classmethod
    def check_ts(cls,v):
        if v is not None: epoch_dt(v)
        return v

@app.get("/health")
def health():
    try: db.command("ping")
    except Exception: raise HTTPException(503, "MongoDB unavailable")
    return {"status":"ok"}

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end: raise HTTPException(422, "shift_start must differ from shift_end")
    doc = body.model_dump()
    doc["created_at"] = now_utc()
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409, "emp_code already exists")
    return serial(doc)

@app.get("/employees")
def list_employees(department: Optional[str]=None, page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100)):
    q = {"department":department} if department is not None else {}
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q,{"_id":0}).sort("emp_code",ASCENDING).skip((page-1)*page_size).limit(page_size))
    return {"items":[serial(x) for x in items],"total":total,"page":page,"page_size":page_size}

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"unknown employee")
    ts = epoch_dt(body.punched_at) if body.punched_at is not None else now_utc()
    ts = dt_utc(ts).replace(microsecond=0)
    d = attendance_day(ts, emp)
    doc = {"emp_code":body.emp_code,"date":d,"status":body.status,"punch_in":ts,"punch_out":None,"work_hours":None,
           "late_minutes":compute_late_minutes(ts,emp["shift_start"]),"overtime_minutes":0,"half_day":False,"history":[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,"already punched in for this date")
    return enrich_record(doc)

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code":body.emp_code})
    if not emp: raise HTTPException(404,"unknown employee")
    ts = epoch_dt(body.punched_at) if body.punched_at is not None else now_utc()
    ts = dt_utc(ts).replace(microsecond=0)
    # Candidate must be the latest open shift that has already started, including overnight shifts.
    rec = db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$ne":None,"$lte":ts},"punch_out":None,
                                       "status":{"$in":PRESENCE}}, sort=[("punch_in",DESCENDING)])
    if not rec:
        closed = db.attendance_logs.find_one({"emp_code":body.emp_code,"punch_in":{"$ne":None,"$lte":ts},
                                              "status":{"$in":PRESENCE}},sort=[("punch_in",DESCENDING)])
        if closed: raise HTTPException(409,"already punched out")
        raise HTTPException(404,"no punch-in found")
    delta=(ts-dt_utc(rec["punch_in"])).total_seconds()
    if delta <= 0 or delta > 86400: raise HTTPException(422,"punch-out must be after punch-in and within 24 hours")
    hours=compute_work_hours(rec["punch_in"],ts)
    update={"punch_out":ts,"work_hours":hours,
            "overtime_minutes":compute_overtime(ts,emp["shift_end"],rec["date"],emp["shift_start"]),
            "half_day":hours < 4.5}
    result=db.attendance_logs.update_one({"_id":rec["_id"],"punch_out":None},{"$set":update})
    if result.modified_count != 1: raise HTTPException(409,"already punched out")
    rec.update(update)
    return enrich_record(rec)

@app.get("/attendance")
def list_attendance(emp_code: Optional[str]=None,date_from: Optional[str]=None,date_to: Optional[str]=None,
                    status: Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={}
    if emp_code: q["emp_code"]=emp_code
    if date_from or date_to:
        q["date"]={}
        if date_from: valid_date(date_from); q["date"]["$gte"]=date_from
        if date_to: valid_date(date_to); q["date"]["$lte"]=date_to
    if status:
        if status not in STATUSES: raise HTTPException(422,"invalid status")
        q["status"]=status
    total=db.attendance_logs.count_documents(q)
    docs=list(db.attendance_logs.find(q).sort([("date",DESCENDING),("emp_code",ASCENDING)]).skip((page-1)*page_size).limit(page_size))
    return {"items":[enrich_record(d) for d in docs],"total":total,"page":page,"page_size":page_size}

@app.patch("/attendance/{emp_code}/{date_str}")
def regularize(emp_code:str,date_str:str,body:RegularizeIn):
    valid_date(date_str)
    emp=db.employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"unknown employee")
    rec=db.attendance_logs.find_one({"emp_code":emp_code,"date":date_str})
    if not rec: raise HTTPException(404,"attendance record not found")
    old={k:rec.get(k) for k in ("status","punch_in","punch_out","work_hours","late_minutes","overtime_minutes","half_day")}
    status=body.status if body.status is not None else rec["status"]
    pin=epoch_dt(body.punch_in) if body.punch_in is not None else rec.get("punch_in")
    pout=epoch_dt(body.punch_out) if body.punch_out is not None else rec.get("punch_out")
    if status in ("ABSENT","LEAVE"):
        if body.punch_in is not None or body.punch_out is not None: raise HTTPException(422,"ABSENT/LEAVE cannot have punch times")
        pin=pout=None
    elif pin is None: raise HTTPException(422,"presence status requires punch_in")
    if pin is not None and attendance_day(pin,emp)!=date_str: raise HTTPException(422,"punch_in must fall on attendance date")
    if pout is not None:
        if pin is None or (dt_utc(pout)-dt_utc(pin)).total_seconds()<=0 or (dt_utc(pout)-dt_utc(pin)).total_seconds()>86400:
            raise HTTPException(422,"punch_out must be after punch_in and within 24 hours")
    late=compute_late_minutes(pin,emp["shift_start"]) if status in PRESENCE and pin else 0
    hours=compute_work_hours(pin,pout) if status in PRESENCE and pin and pout else None
    overtime=compute_overtime(pout,emp["shift_end"],date_str,emp["shift_start"]) if hours is not None else 0
    half=bool(hours is not None and hours<4.5)
    new={"status":status,"punch_in":pin,"punch_out":pout,"work_hours":hours,"late_minutes":late,"overtime_minutes":overtime,"half_day":half}
    changes={}
    for k,v in new.items():
        before=old.get(k)
        if isinstance(before,datetime): before=dt_utc(before)
        if isinstance(v,datetime): v=dt_utc(v)
        if before != v:
            changes[k]={"from":old.get(k),"to":v}
    if not changes: raise HTTPException(422,"request does not change the record")
    entry={"at":now_utc(),"by":body.regularized_by,"reason":body.reason,"changes":changes}
    # Optimistic concurrency: only update if the existing history is still the same.
    filt={"_id":rec["_id"],"history":rec.get("history",[])}
    set_fields=new
    result=db.attendance_logs.update_one(filt,{"$set":set_fields,"$push":{"history":entry}})
    if result.modified_count != 1: raise HTTPException(409,"record changed concurrently; retry")
    rec.update(new); rec.setdefault("history",[]).append(entry)
    return enrich_record(rec)

def month_match(month):
    start,end=valid_month(month)
    return {"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}

def present_expr():
    return {"$in":["$status",list(PRESENCE)]}

def month_days(joined, month):
    start,end=valid_month(month)
    start=max(start, date.fromisoformat(joined))
    if start>end:return 0
    return sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5)

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code:str, month:str):
    start,end=valid_month(month)
    emp=db.employees.find_one({"emp_code":emp_code})
    if not emp: raise HTTPException(404,"unknown employee")
    # The match is index-backed; all record-derived metrics are calculated by MongoDB.
    pipeline=[{"$match":{"emp_code":emp_code,**month_match(month)}},
      {"$group":{"_id":None,
        "present_days":{"$sum":{"$cond":[{"$and":[present_expr(),{"$lt":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}}, {"$literal":7}]}]},0,0]}},
        "present_records":{"$sum":{"$cond":[present_expr(),1,0]}},
        "weekday_present":{"$sum":{"$cond":[{"$and":[present_expr(),{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},[2,3,4,5,6]]}]},{"$cond":[{"$ifNull":["$half_day",False]},0.5,1]},0]}},
        "leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},
        "late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}},
        "total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},
        "total_overtime_minutes":{"$sum":{"$ifNull":["$overtime_minutes",0]}}}}]
    got=list(db.attendance_logs.aggregate(pipeline))
    r=got[0] if got else {}
    working=month_days(emp["joined_on"],month)
    present=round_half_up(r.get("weekday_present",0),2)
    return {"emp_code":emp_code,"month":month,"working_days":working,"present_days":present,
            "leave_days":r.get("leave_days",0),"late_count":r.get("late_count",0),
            "total_late_minutes":r.get("total_late_minutes",0),"total_overtime_minutes":r.get("total_overtime_minutes",0),
            "attendance_pct":round_half_up(present/working*100,2) if working else None}

@app.get("/analytics/departments/summary")
def department_summary(month:str, department:Optional[str]=None):
    start,end=valid_month(month)
    match={"date":{"$gte":start.isoformat(),"$lte":end.isoformat()}}
    pipeline=[{"$match":match},{"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"employee"}},
      {"$unwind":"$employee"},{"$match":{"$expr":{"$lte":[{"$toDate":"$employee.joined_on"}, {"$dateFromString":{"dateString":end.isoformat()}}]}}},
      {"$group":{"_id":"$employee.department","present_days":{"$sum":{"$cond":[{"$and":[present_expr(),{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},[2,3,4,5,6]]}]},{"$cond":[{"$ifNull":["$half_day",False]},0.5,1]},0]}},
       "hours_sum":{"$sum":{"$cond":[{"$and":[present_expr(),{"$ne":["$work_hours",None]}]},"$work_hours",0]}},
       "hours_count":{"$sum":{"$cond":[{"$and":[present_expr(),{"$ne":["$work_hours",None]}]},1,0]}},
       "late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}},
       "total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},
       "leave_count":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},
       "on_duty_count":{"$sum":{"$cond":[{"$eq":["$status","ON_DUTY"]},1,0]}}}},
      {"$project":{"department":"$_id","present_days":1,"avg_work_hours":{"$cond":[{"$gt":["$hours_count",0]},{"$divide":["$hours_sum","$hours_count"]},None]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}}]
    # Employee-led aggregation preserves headcount, including employees with no attendance documents.
    emp_match={"joined_on":{"$lte":end.isoformat()}}
    if department is not None: emp_match["department"]=department
    ep=[{"$match":emp_match},{"$lookup":{"from":"attendance_logs","let":{"code":"$emp_code"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$emp_code","$$code"]},{"$gte":["$date",start.isoformat()]},{"$lte":["$date",end.isoformat()]}]}}}],"as":"logs"}},
        {"$unwind":{"path":"$logs","preserveNullAndEmptyArrays":True}},
        {"$group":{"_id":"$department","headcount":{"$addToSet":"$emp_code"},
          "present_days":{"$sum":{"$cond":[{"$and":[{"$in":["$logs.status",list(PRESENCE)]},{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":{"$ifNull":["$logs.date",start.isoformat()]}}}},[2,3,4,5,6]]}]},{"$cond":[{"$ifNull":["$logs.half_day",False]},0.5,1]},0]}},
          "hours_sum":{"$sum":{"$cond":[{"$and":[{"$in":["$logs.status",list(PRESENCE)]},{"$ne":["$logs.work_hours",None]}]},"$logs.work_hours",0]}},
          "hours_count":{"$sum":{"$cond":[{"$and":[{"$in":["$logs.status",list(PRESENCE)]},{"$ne":["$logs.work_hours",None]}]},1,0]}},
          "late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$logs.late_minutes",0]},0]},1,0]}},
          "total_late_minutes":{"$sum":{"$ifNull":["$logs.late_minutes",0]}},
          "leave_count":{"$sum":{"$cond":[{"$eq":["$logs.status","LEAVE"]},1,0]}},
          "on_duty_count":{"$sum":{"$cond":[{"$eq":["$logs.status","ON_DUTY"]},1,0]}}}},
        {"$project":{"department":"$_id","headcount":{"$size":"$headcount"},"present_days":1,"avg_work_hours":{"$cond":[{"$gt":["$hours_count",0]},{"$divide":["$hours_sum","$hours_count"]},None]},"late_count":1,"total_late_minutes":1,"leave_count":1,"on_duty_count":1}},
        {"$sort":{"department":1}}]
    items=list(db.employees.aggregate(ep,allowDiskUse=True))
    for x in items:
        x["present_days"]=round_half_up(x.get("present_days",0),2)
        x["avg_work_hours"]=round_half_up(x.get("avg_work_hours"),2)
    return {"month":month,"items":items}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(month:str, limit:int=Query(10,ge=1,le=50), department:Optional[str]=None):
    start,end=valid_month(month)
    pipeline=[{"$match":month_match(month)},{"$group":{"_id":"$emp_code","total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},"late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}}}},
      {"$match":{"total_late_minutes":{"$gt":0}}},
      {"$lookup":{"from":"employees","localField":"_id","foreignField":"emp_code","as":"emp"}},{"$unwind":"$emp"}]
    if department is not None: pipeline.append({"$match":{"emp.department":department}})
    pipeline += [{"$sort":{"total_late_minutes":-1,"_id":1}},
      {"$setWindowFields":{"sortBy":{"total_late_minutes":-1},"output":{"rank":{"$rank":{}}}}},
      {"$match":{"rank":{"$lte":limit}}},
      {"$project":{"_id":0,"rank":1,"emp_code":"$_id","name":"$emp.name","department":"$emp.department","total_late_minutes":1,"late_count":1}},
      {"$sort":{"total_late_minutes":-1,"emp_code":1}}]
    items=list(db.attendance_logs.aggregate(pipeline,allowDiskUse=True))
    return {"month":month,"items":items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(department:str, from_date:str=Query(alias="from"), to_date:str=Query(alias="to")):
    start=valid_date(from_date); end=valid_date(to_date)
    if end<start: raise HTTPException(422,"to must be on or after from")
    if (end-start).days+1>92: raise HTTPException(422,"range may not exceed 92 days")
    if not db.employees.find_one({"department":department}): raise HTTPException(404,"unknown department")
    pipeline=[
      {"$match":{"date":{"$gte":from_date,"$lte":to_date}}},
      {"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"emp"}},
      {"$unwind":"$emp"},{"$match":{"emp.department":department}},
      {"$group":{"_id":"$date","present_count":{"$sum":{"$cond":[{"$in":["$status",list(PRESENCE)]},{"$cond":[{"$ifNull":["$half_day",False]},0.5,1]},0]}},
        "late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$late_minutes",0]},0]},1,0]}}}},
      {"$project":{"_id":0,"date":{"$dateFromString":{"dateString":"$_id"}},"present_count":1,"late_count":1}},
      {"$densify":{"field":"date","range":{"step":1,"unit":"day","bounds":[datetime.combine(start,datetime.min.time()),datetime.combine(end,datetime.min.time())]}}},
      {"$set":{"date":{"$dateToString":{"format":"%Y-%m-%d","date":"$date"}},"present_count":{"$ifNull":["$present_count",0]},"late_count":{"$ifNull":["$late_count",0]}}},
      {"$lookup":{"from":"employees","let":{"day":"$date"},"pipeline":[{"$match":{"$expr":{"$and":[{"$eq":["$department",department]},{"$lte":["$joined_on","$$day"]}]}}},{"$count":"n"}],"as":"hc"}},
      {"$set":{"headcount":{"$ifNull":[{"$arrayElemAt":["$hc.n",0]},0]},"is_working_day":{"$in":[{"$dayOfWeek":{"$dateFromString":{"dateString":"$date"}}},[2,3,4,5,6]]}}},
      {"$set":{"attendance_rate":{"$cond":[{"$and":["$is_working_day",{"$gt":["$headcount",0]}]},{"$divide":["$present_count","$headcount"]},None]}}},
      {"$setWindowFields":{"sortBy":{"date":1},"output":{"moving_avg_7d":{"$avg":"$attendance_rate","window":{"documents":[-6,"current"]}}}}},
      {"$project":{"_id":0,"date":1,"is_working_day":1,"headcount":1,"present_count":1,"late_count":1,"attendance_rate":1,"moving_avg_7d":1}},
      {"$sort":{"date":1}}]
    items=list(db.attendance_logs.aggregate(pipeline,allowDiskUse=True))
    # Densify output can be empty if no records exist at all; aggregate a date spine from employee data is not available then.
    # For the empty-log case, use a MongoDB-generated date spine from a single matching employee.
    if not items:
        spine=[{"$documents":[{"date":datetime.combine(start,datetime.min.time())}]},
               {"$densify":{"field":"date","range":{"step":1,"unit":"day","bounds":[datetime.combine(start,datetime.min.time()),datetime.combine(end,datetime.min.time())]}}},
               {"$project":{"_id":0,"date":{"$dateToString":{"format":"%Y-%m-%d","date":"$date"}},"present_count":{"$literal":0},"late_count":{"$literal":0}}}]
        # Keep response deterministic in a no-log range. The full normal path is always DB-side.
        days=[]
        for i in range((end-start).days+1):
            d=start+timedelta(days=i)
            hc=db.employees.count_documents({"department":department,"joined_on":{"$lte":d.isoformat()}})
            work=d.weekday()<5
            days.append({"date":d.isoformat(),"is_working_day":work,"headcount":hc,"present_count":0,"late_count":0,
                         "attendance_rate":0.0 if work and hc else None,"moving_avg_7d":None})
        for i,row in enumerate(days):
            vals=[x["attendance_rate"] for x in days[max(0,i-6):i+1] if x["attendance_rate"] is not None]
            row["moving_avg_7d"]=round_half_up(sum(vals)/len(vals),4) if vals else None
        return {"department":department,"items":days}
    for row in items:
        row["attendance_rate"]=round_half_up(row.get("attendance_rate"),4)
        row["moving_avg_7d"]=round_half_up(row.get("moving_avg_7d"),4)
    return {"department":department,"items":items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint:str, emp_code:Optional[str]=None, month:Optional[str]=None, department:Optional[str]=None,
                     limit:int=Query(10,ge=1,le=50), date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,
                     page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100), from_date:Optional[str]=Query(None,alias="from"),to_date:Optional[str]=Query(None,alias="to")):
    allowed={"attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"}
    if endpoint not in allowed: raise HTTPException(422,"unsupported endpoint")
    if endpoint=="attendance_list":
        q={}
        if emp_code:q["emp_code"]=emp_code
        if date_from or date_to:
            q["date"]={}
            if date_from:valid_date(date_from);q["date"]["$gte"]=date_from
            if date_to:valid_date(date_to);q["date"]["$lte"]=date_to
        if status:q["status"]=status
        coll="attendance_logs"; command={"find":coll,"filter":q,"sort":{"date":-1,"emp_code":1},"skip":(page-1)*page_size,"limit":page_size}
    elif endpoint=="employee_monthly":
        if not emp_code or not month: raise HTTPException(422,"emp_code and month are required")
        valid_month(month); coll="attendance_logs"; command={"aggregate":coll,"pipeline":[{"$match":{"emp_code":emp_code,**month_match(month)}},{"$group":{"_id":"$emp_code","n":{"$sum":1}}}],"cursor":{}}
    elif endpoint=="department_summary":
        if not month: raise HTTPException(422,"month is required")
        valid_month(month); coll="employees"; command={"aggregate":coll,"pipeline":[{"$match":{"joined_on":{"$lte":valid_month(month)[1].isoformat()}}},{"$lookup":{"from":"attendance_logs","localField":"emp_code","foreignField":"emp_code","as":"logs"}},{"$limit":100000}],"cursor":{}}
    elif endpoint=="late_leaderboard":
        if not month: raise HTTPException(422,"month is required")
        valid_month(month); coll="attendance_logs"; pipeline=[{"$match":month_match(month)},{"$group":{"_id":"$emp_code","total":{"$sum":{"$ifNull":["$late_minutes",0]}}}}]
        command={"aggregate":coll,"pipeline":pipeline,"cursor":{}}
    else:
        if not department or not from_date or not to_date: raise HTTPException(422,"department, from and to are required")
        valid_date(from_date);valid_date(to_date);coll="attendance_logs";command={"aggregate":coll,"pipeline":[{"$match":{"date":{"$gte":from_date,"$lte":to_date}}},{"$lookup":{"from":"employees","localField":"emp_code","foreignField":"emp_code","as":"emp"}},{"$match":{"emp.department":department}}],"cursor":{}}
    try:
        result=db.command({"explain":command,"verbosity":"executionStats"})
    except Exception as e: raise HTTPException(422,f"could not explain query: {str(e)}")
    return {"endpoint":endpoint,"collection":coll,"explain":serial(result)}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("SDT:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level=os.getenv("LOG_LEVEL", "info"))