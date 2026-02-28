"""Common type aliases for the SP5 backend API."""
from typing import Any

# A single row from a DBF table (field_name -> value)
DBFRow = dict[str, Any]

# Domain record aliases
EmployeeRecord = dict[str, Any]
ShiftRecord = dict[str, Any]
GroupRecord = dict[str, Any]
ScheduleEntry = dict[str, Any]
AbsenceRecord = dict[str, Any]
BookingRecord = dict[str, Any]

# List aliases
EmployeeList = list[EmployeeRecord]
ShiftList = list[ShiftRecord]
ScheduleList = list[ScheduleEntry]
DBFRowList = list[DBFRow]
