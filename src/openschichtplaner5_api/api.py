# src/openschichtplaner5_api/api.py
"""
REST API wrapper for Schichtplaner5 data.
Provides a FastAPI-based web service for data access.
"""

from fastapi import FastAPI, HTTPException, Query, Path, Depends
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import date, datetime
from pathlib import Path as FilePath
import json
import io

from libopenschichtplaner5.query_engine import QueryEngine
from libopenschichtplaner5.reports import ReportGenerator
from libopenschichtplaner5.export import DataExporter, ExportFormat
from libopenschichtplaner5.utils.validation import DataValidator
from libopenschichtplaner5.performance import performance_monitor, monitor_performance
from libopenschichtplaner5.exceptions import DataNotFoundError
from .analytics import create_analytics_router


# Pydantic models for API
class QueryFilter(BaseModel):
    """Query filter definition."""
    field: str
    operator: str = "="
    value: Any


class QueryRequest(BaseModel):
    """Query request body."""
    table: str
    filters: List[QueryFilter] = []
    joins: List[str] = []
    limit: Optional[int] = None
    offset: Optional[int] = None
    order_by: Optional[str] = None
    order_desc: bool = False


class EmployeeResponse(BaseModel):
    """Employee response model."""
    id: int
    name: str
    firstname: str
    position: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    empstart: Optional[date]
    empend: Optional[date]
    # Color fields for employee display
    cfglabel: Optional[int] = None  # Foreground/text color (RGB integer)
    cbklabel: Optional[int] = None  # Background color (RGB integer)
    cbksched: Optional[int] = None  # Schedule background color (RGB integer)

    class Config:
        from_attributes = True


class AbsenceResponse(BaseModel):
    """Absence response model."""
    id: int
    employee_id: int
    date: date
    leave_type_id: int
    type: int

    class Config:
        from_attributes = True


class ExportRequest(BaseModel):
    """Export request body."""
    table: str
    format: str = Field(..., pattern="^(csv|json|excel|html|markdown)$")
    filters: List[QueryFilter] = []
    fields: Optional[List[str]] = None


class ReportRequest(BaseModel):
    """Report request body."""
    report_type: str = Field(..., pattern="^(absence|staffing|shifts|overtime)$")
    parameters: Dict[str, Any] = {}


class SchichtplanerAPI:
    """REST API for Schichtplaner5 data."""

    def __init__(self, dbf_dir: FilePath, title: str = "Schichtplaner5 API",
                 version: str = "1.0.0"):
        self.app = FastAPI(
            title=title,
            version=version,
            description="REST API for accessing Schichtplaner5 data"
        )

        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Initialize components
        self.dbf_dir = dbf_dir
        self.engine = None
        self.report_generator = None
        self.exporter = DataExporter()
        self.validator = DataValidator()

        # Setup routes
        self._setup_routes()

        # Initialize engine immediately
        self._initialize_engine()
        
        # Add analytics router
        if self.engine:
            analytics_router = create_analytics_router(self.engine)
            self.app.include_router(analytics_router)

    def _initialize_engine(self):
        """Initialize query engine."""
        try:
            print(f"Initializing engine with DBF directory: {self.dbf_dir}")
            self.engine = QueryEngine(self.dbf_dir)
            self.report_generator = ReportGenerator(self.engine)
            print(f"Engine initialized with {len(self.engine.loaded_tables)} tables")
        except Exception as e:
            print(f"Failed to initialize engine: {e}")
            raise

    def _setup_routes(self):
        """Setup all API routes."""

        # Health check
        @self.app.get("/health")
        async def health_check():
            return {
                "status": "healthy",
                "tables_loaded": len(self.engine.loaded_tables) if self.engine else 0,
                "timestamp": datetime.now().isoformat()
            }

        # Table info
        @self.app.get("/tables")
        async def list_tables():
            """List all available tables."""
            if not self.engine:
                raise HTTPException(status_code=503, detail="Engine not initialized")

            return {
                "tables": [
                    {
                        "name": name,
                        "records": len(records)
                    }
                    for name, records in self.engine.loaded_tables.items()
                ]
            }

        @self.app.get("/tables/{table_name}")
        async def get_table_info(table_name: str):
            """Get information about a specific table."""
            if table_name not in self.engine.loaded_tables:
                raise HTTPException(status_code=404, detail=f"Table {table_name} not found")

            records = self.engine.loaded_tables[table_name]

            # Get sample record for field info
            fields = []
            if records:
                sample = records[0]
                for attr in dir(sample):
                    if not attr.startswith('_') and not callable(getattr(sample, attr)):
                        value = getattr(sample, attr)
                        fields.append({
                            "name": attr,
                            "type": type(value).__name__
                        })

            return {
                "name": table_name,
                "record_count": len(records),
                "fields": fields
            }

        # Query endpoint
        @self.app.post("/query")
        async def execute_query(request: QueryRequest):
            """Execute a query."""
            try:
                query = self.engine.query().select(request.table)

                # Apply filters
                for filter in request.filters:
                    query = query.where(filter.field, filter.operator, filter.value)

                # Apply joins
                for join_table in request.joins:
                    query = query.join(join_table)

                # Apply ordering
                if request.order_by:
                    query = query.order_by(request.order_by, not request.order_desc)

                # Apply pagination
                if request.offset:
                    query = query.offset(request.offset)
                if request.limit:
                    query = query.limit(request.limit)

                # Execute
                result = query.execute()

                return {
                    "success": True,
                    "count": getattr(result, 'count', len(result.to_dict()) if hasattr(result, 'to_dict') else 0),
                    "data": result.to_dict() if hasattr(result, 'to_dict') else [],
                    "execution_time": getattr(result, 'execution_time', 0)
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")

        # Employee endpoints
        @self.app.get("/employees", response_model=List[EmployeeResponse])
        async def list_employees(
                limit: int = Query(100, ge=1, le=1000),
                offset: int = Query(0, ge=0),
                position: Optional[str] = None,
                active_only: bool = False
        ):
            """List employees with filtering."""
            query = self.engine.query().select("5EMPL")

            if position:
                query = query.where("position", "=", position)
            if active_only:
                query = query.where("empend", "is_null", None)

            query = query.offset(offset).limit(limit)
            result = query.execute()

            return result.to_dict()

        @self.app.get("/employees/{employee_id}")
        async def get_employee(employee_id: int = Path(..., ge=1)):
            """Get employee details."""
            try:
                profile = self.engine.get_employee_full_profile(employee_id)
                return profile
            except DataNotFoundError:
                raise HTTPException(status_code=404, detail=f"Employee {employee_id} not found")

        @self.app.get("/employees/{employee_id}/schedule")
        async def get_employee_schedule(
                employee_id: int = Path(..., ge=1),
                start_date: Optional[date] = None,
                end_date: Optional[date] = None
        ):
            """Get employee schedule."""
            schedule = self.engine.get_employee_schedule(employee_id, start_date, end_date)
            return {
                "employee_id": employee_id,
                "schedule": schedule
            }

        @self.app.get("/employees/{employee_id}/absences", response_model=List[AbsenceResponse])
        async def get_employee_absences(
                employee_id: int = Path(..., ge=1),
                year: Optional[int] = None
        ):
            """Get employee absences."""
            query = self.engine.query().select("5ABSEN").where("employee_id", "=", employee_id)

            if year:
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                query = query.where_date_range("date", start_date, end_date)

            result = query.execute()
            return result.to_dict()

        # Group endpoints
        @self.app.get("/groups")
        async def list_groups():
            """List all groups."""
            result = self.engine.query().select("5GROUP").order_by("name").execute()
            return result.to_dict()

        @self.app.get("/groups/{group_id}/members")
        async def get_group_members(group_id: int = Path(..., ge=1)):
            """Get members of a group."""
            members = self.engine.get_group_members(group_id)
            return {
                "group_id": group_id,
                "members": members
            }

        # Shift endpoints
        @self.app.get("/shifts")
        async def list_shifts():
            """List all shift definitions."""
            result = self.engine.query().select("5SHIFT").order_by("name").execute()
            return result.to_dict()

        # Schedule endpoints for different views
        @self.app.get("/schedule/dienstplan")
        async def get_dienstplan(
                year: int = Query(2025),
                month: int = Query(4, ge=1, le=12),
                limit: Optional[int] = Query(None, ge=1, le=1000)
        ):
            """Get Dienstplan view - monthly schedule for all employees."""
            try:
                # Get employee shift assignments for the month
                start_date = date(year, month, 1)
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                end_date = date(year, month, last_day)
                
                # Get only active employees (empend is null)
                employees_result = self.engine.query().select("5EMPL").where("empend", "is_null", None).execute()
                employees = employees_result.to_dict()
                
                # Apply limit only if explicitly requested (optional for debugging/performance)
                if limit is not None and limit < len(employees):
                    employees = employees[:limit]
                
                # Get shift assignments - COMBINE main and special assignments
                mashi_query = self.engine.query().select("5MASHI")
                mashi_result = mashi_query.execute()
                mashi_assignments = mashi_result.to_dict()
                
                spshi_query = self.engine.query().select("5SPSHI")
                spshi_result = spshi_query.execute() 
                spshi_assignments = spshi_result.to_dict()
                
                shift_assignments = mashi_assignments + spshi_assignments
                print(f"Found {len(mashi_assignments)} main + {len(spshi_assignments)} special = {len(shift_assignments)} total shift assignments")
                
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Organize data by employee and date
                employee_schedules = {}
                for emp in employees:
                    employee_schedules[emp['id']] = {
                        'employee': emp,
                        'schedule': {}
                    }
                
                # Process shift assignments
                processed_count = 0
                for shift in shift_assignments:
                    emp_id = shift.get('employee_id')
                    if emp_id in employee_schedules:
                        shift_date = shift.get('date')
                        if shift_date:
                            # Handle different date formats
                            if isinstance(shift_date, str):
                                try:
                                    # Try different date formats
                                    if 'T' in shift_date:
                                        shift_date = datetime.fromisoformat(shift_date.replace('T', ' ')).date()
                                    else:
                                        shift_date = datetime.strptime(shift_date[:10], '%Y-%m-%d').date()
                                except (ValueError, TypeError):
                                    continue
                            elif hasattr(shift_date, 'date'):
                                shift_date = shift_date.date()
                            
                            if start_date <= shift_date <= end_date:
                                day = shift_date.day
                                shift_def = shift_definitions.get(shift.get('shift_id'), {})
                                shift_code = shift_def.get('shortname') or shift_def.get('name', 'T')[:1] if shift_def.get('name') else 'T'
                                
                                employee_schedules[emp_id]['schedule'][day] = {
                                    'shift_id': shift.get('shift_id'),
                                    'shift_code': shift_code,
                                    'shift_name': shift_def.get('name', 'Tagschicht'),
                                    'date': shift_date.isoformat(),
                                    'colors': {
                                        'bar': shift_def.get('colorbar', 65535),
                                        'background': shift_def.get('colorbk', 65535),
                                        'text': shift_def.get('colortext', 0)
                                    }
                                }
                                processed_count += 1
                
                print(f"Processed {processed_count} shifts for {month}/{year}")
                
                return {
                    'year': year,
                    'month': month,
                    'days_in_month': last_day,
                    'employees': list(employee_schedules.values())
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating dienstplan: {str(e)}")

        @self.app.get("/schedule/einsatzplan")
        async def get_einsatzplan(
                year: int = Query(2025),
                month: int = Query(4, ge=1, le=12)
        ):
            """Get Einsatzplan view - shifts by type (rows) and days (columns) with employee names in cells."""
            try:
                # Get month date range
                start_date = date(year, month, 1)
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                end_date = date(year, month, last_day)
                
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Get shift assignments for the month - COMBINE all relevant types
                # 5MASHI = regular planned shifts, 5SPSHI = changes/overrides/special assignments
                mashi_query = self.engine.query().select("5MASHI")
                mashi_result = mashi_query.execute()
                mashi_assignments = mashi_result.to_dict()
                
                spshi_query = self.engine.query().select("5SPSHI") 
                spshi_result = spshi_query.execute()
                spshi_assignments = spshi_result.to_dict()
                
                # Add absences as special events
                absences_query = self.engine.query().select("5ABSEN")
                absences_result = absences_query.execute()
                absences_data = absences_result.to_dict()
                
                # Convert absences to shift-like format for display
                absence_assignments = []
                for absence in absences_data:
                    absence_assignments.append({
                        'employee_id': absence.get('employee_id'),
                        'date': absence.get('date'),
                        'shift_id': 99999,  # Special ID for absences
                        'type': 'absence'
                    })
                
                # Combine all assignment types
                shift_assignments = mashi_assignments + spshi_assignments + absence_assignments
                
                print(f"DEBUG: Loaded {len(mashi_assignments)} main + {len(spshi_assignments)} special + {len(absence_assignments)} absences = {len(shift_assignments)} total")
                
                # Get employee data
                employees_query = self.engine.query().select("5EMPL")
                employees_result = employees_query.execute()
                employees_dict = {emp['id']: emp for emp in employees_result.to_dict()}
                
                # Create matrix: shift_types[shift_name][day] = [employee_names]
                shift_matrix = {}
                
                processed_count = 0
                tagdienst_count = 0
                date_parse_errors = 0
                sample_dates = []
                
                for i, shift in enumerate(shift_assignments):
                    shift_date = shift.get('date')
                    if shift_date:
                        # Collect sample dates for debugging
                        if len(sample_dates) < 10:
                            sample_dates.append(f"{type(shift_date).__name__}: {repr(shift_date)}")
                        
                        # Handle different date formats
                        original_date = shift_date
                        if isinstance(shift_date, str):
                            try:
                                # Try different date formats
                                if 'T' in shift_date:
                                    shift_date = datetime.fromisoformat(shift_date.replace('T', ' ')).date()
                                elif '-' in shift_date and len(shift_date) >= 10:
                                    shift_date = datetime.strptime(shift_date[:10], '%Y-%m-%d').date()
                                elif '/' in shift_date:
                                    # Try different formats like MM/DD/YYYY or DD/MM/YYYY
                                    if len(shift_date.split('/')) == 3:
                                        parts = shift_date.split('/')
                                        if len(parts[2]) == 4:  # YYYY format
                                            shift_date = datetime.strptime(shift_date, '%m/%d/%Y').date()
                                        else:
                                            shift_date = datetime.strptime(shift_date, '%d/%m/%y').date()
                                elif '.' in shift_date:
                                    # Try German format DD.MM.YYYY
                                    shift_date = datetime.strptime(shift_date, '%d.%m.%Y').date()
                                else:
                                    # Try to parse as YYYYMMDD
                                    if len(shift_date) == 8 and shift_date.isdigit():
                                        shift_date = datetime.strptime(shift_date, '%Y%m%d').date()
                                    else:
                                        raise ValueError(f"Unknown date format: {shift_date}")
                            except (ValueError, TypeError) as e:
                                # Log problematic dates for first few errors
                                if date_parse_errors < 5:
                                    print(f"DEBUG: Date parse error for '{original_date}' (type: {type(original_date)}): {e}")
                                date_parse_errors += 1
                                continue
                        elif hasattr(shift_date, 'date'):
                            shift_date = shift_date.date()
                        elif hasattr(shift_date, 'year'):
                            # Already a date object
                            pass
                        else:
                            if date_parse_errors < 5:
                                print(f"DEBUG: Unknown date type: {type(shift_date)} = {repr(shift_date)}")
                            date_parse_errors += 1
                            continue
                        
                        if start_date <= shift_date <= end_date:
                            shift_id = shift.get('shift_id')
                            employee_id = shift.get('employee_id')
                            
                            # Handle special cases (absences)
                            if shift.get('type') == 'absence':
                                shift_def = {'name': 'Abwesenheit', 'startend0': '00:00', 'duration0': 0}
                                shift_name = 'Abwesenheit'
                            else:
                                shift_def = shift_definitions.get(shift_id, {})
                                shift_name = shift_def.get('name', 'Unbekannte Schicht')
                            
                            employee = employees_dict.get(employee_id, {})
                            day = shift_date.day
                            
                            processed_count += 1
                            
                            # Count Tagdienst entries
                            if 'Tagdienst' in shift_name.upper() or 'TD' in shift_name.upper():
                                tagdienst_count += 1
                            
                            if shift_name not in shift_matrix:
                                shift_matrix[shift_name] = {
                                    'shift_definition': shift_def,
                                    'days': {}
                                }
                            
                            if day not in shift_matrix[shift_name]['days']:
                                shift_matrix[shift_name]['days'][day] = []
                            
                            shift_matrix[shift_name]['days'][day].append({
                                'id': employee_id,
                                'name': employee.get('name', 'Unknown'),
                                'firstname': employee.get('firstname', '')
                            })
                        else:
                            date_parse_errors += 1
                
                print(f"DEBUG: Processed {processed_count} shifts in date range {start_date} to {end_date}")
                print(f"DEBUG: Found {tagdienst_count} Tagdienst shifts")
                print(f"DEBUG: Date parse errors: {date_parse_errors}")
                print(f"DEBUG: Unique shift types: {len(shift_matrix)}")
                print(f"DEBUG: Sample date formats: {sample_dates}")
                
                # Sort shifts by start time
                sorted_shifts = {}
                for shift_name in sorted(shift_matrix.keys(), key=lambda x: shift_matrix[x]['shift_definition'].get('startend0', '00:00')):
                    sorted_shifts[shift_name] = shift_matrix[shift_name]
                
                return {
                    'year': year,
                    'month': month,
                    'days_in_month': last_day,
                    'shifts': sorted_shifts
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating einsatzplan: {str(e)}")

        @self.app.get("/schedule/dienstplan-range")
        async def get_dienstplan_range(
                start_date: date = Query(..., description="Start date for the range"),
                end_date: date = Query(..., description="End date for the range"),
                limit: Optional[int] = Query(None, ge=1, le=500)
        ):
            """Get Dienstplan view for a specific date range - optimized for infinite scrolling."""
            try:
                # Get only active employees (empend is null)
                employees_query = self.engine.query().select("5EMPL").where("empend", "is_null", None)
                if limit is not None:
                    employees_query = employees_query.limit(limit)
                employees_result = employees_query.execute()
                employees = employees_result.to_dict()
                
                # Get shift assignments for the date range
                shift_assignments = []
                try:
                    main_shifts_result = self.engine.query().select("5MASHI")\
                        .where_date_range("date", start_date, end_date).execute()
                    shift_assignments.extend(main_shifts_result.to_dict())
                    
                    special_shifts_result = self.engine.query().select("5SPSHI")\
                        .where_date_range("date", start_date, end_date).execute()
                    shift_assignments.extend(special_shifts_result.to_dict())
                except Exception as e:
                    logger.warning(f"Could not load shift assignments: {e}")
                
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Organize data by employee
                employee_schedules = {}
                for employee in employees:
                    emp_id = employee['id']
                    employee_schedules[emp_id] = {
                        'id': emp_id,
                        'name': f"{employee.get('firstname', '')} {employee.get('name', '')}".strip() or f"Mitarbeiter {emp_id}",
                        'schedule': []
                    }
                
                # Add shift assignments to employee schedules
                for assignment in shift_assignments:
                    emp_id = assignment.get('employee_id')
                    if emp_id in employee_schedules:
                        shift_def = shift_definitions.get(assignment.get('shift_id'), {})
                        employee_schedules[emp_id]['schedule'].append({
                            'date': assignment.get('date'),
                            'shift_id': assignment.get('shift_id'),
                            'shift_name': shift_def.get('name', 'Schicht'),
                            'start_time': shift_def.get('start_time'),
                            'end_time': shift_def.get('end_time')
                        })
                
                return {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'days_in_range': (end_date - start_date).days + 1,
                    'employees': list(employee_schedules.values())
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating dienstplan range: {str(e)}")

        @self.app.get("/schedule/einsatzplan-range")
        async def get_einsatzplan_range(
                start_date: date = Query(..., description="Start date for the range"),
                end_date: date = Query(..., description="End date for the range")
        ):
            """Get Einsatzplan view for a specific date range - optimized for infinite scrolling."""
            try:
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Get shift assignments for the date range - COMBINE all relevant types
                shift_assignments = []
                try:
                    # Get main assignments (5MASHI) - regular planned shifts
                    main_shifts_result = self.engine.query().select("5MASHI")\
                        .where_date_range("date", start_date, end_date).execute()
                    shift_assignments.extend(main_shifts_result.to_dict())
                    
                    # Get special assignments (5SPSHI) - changes/overrides/special assignments
                    special_shifts_result = self.engine.query().select("5SPSHI")\
                        .where_date_range("date", start_date, end_date).execute()
                    shift_assignments.extend(special_shifts_result.to_dict())
                    
                    # Get absences (5ABSEN) - convert to shift-like format for display
                    absences_result = self.engine.query().select("5ABSEN")\
                        .where_date_range("date", start_date, end_date).execute()
                    absences_data = absences_result.to_dict()
                    
                    # Convert absences to shift-like format for consistent processing
                    for absence in absences_data:
                        shift_assignments.append({
                            'employee_id': absence.get('employee_id'),
                            'date': absence.get('date'),
                            'shift_id': 99999,  # Special ID for absences
                            'type': 'absence'
                        })
                    
                    # Get leave data (5LEAVE) - may not exist in all databases
                    try:
                        leave_result = self.engine.query().select("5LEAVE")\
                            .where_date_range("date", start_date, end_date).execute()
                        leave_data = leave_result.to_dict()
                        
                        # Convert leave to shift-like format for consistent processing
                        for leave in leave_data:
                            shift_assignments.append({
                                'employee_id': leave.get('employee_id'),
                                'date': leave.get('date'),
                                'shift_id': 99998,  # Special ID for leave
                                'type': 'leave'
                            })
                    except Exception as leave_error:
                        # 5LEAVE table might not exist in all installations
                        print(f"DEBUG: Could not load leave data (table may not exist): {leave_error}")
                        
                    # Debug: Report counts of each type loaded
                    main_count = len([a for a in shift_assignments if not a.get('type') and a.get('shift_id') != 99999 and a.get('shift_id') != 99998])
                    absence_count = len([a for a in shift_assignments if a.get('type') == 'absence'])
                    leave_count = len([a for a in shift_assignments if a.get('type') == 'leave'])
                    print(f"DEBUG einsatzplan-range: Loaded {main_count} main/special shifts + {absence_count} absences + {leave_count} leaves = {len(shift_assignments)} total assignments")
                        
                except Exception as e:
                    print(f"Could not load shift assignments: {e}")
                
                # Get employee data for names
                employees_result = self.engine.query().select("5EMPL").execute()
                employee_names = {e['id']: f"{e.get('firstname', '')} {e.get('name', '')}".strip() or f"Mitarbeiter {e['id']}" 
                                for e in employees_result.to_dict()}
                
                # Organize data by shift type
                shift_schedules = {}
                for shift_id, shift_def in shift_definitions.items():
                    shift_schedules[shift_id] = {
                        'id': shift_id,
                        'name': shift_def.get('name', f'Schicht {shift_id}'),
                        'start_time': shift_def.get('start_time'),
                        'end_time': shift_def.get('end_time'),
                        'assignments': []
                    }
                
                # Add special shift types for absences and leave
                shift_schedules[99999] = {
                    'id': 99999,
                    'name': 'Abwesenheit',
                    'start_time': '00:00',
                    'end_time': '00:00',
                    'assignments': []
                }
                shift_schedules[99998] = {
                    'id': 99998,
                    'name': 'Urlaub',
                    'start_time': '00:00',
                    'end_time': '00:00',
                    'assignments': []
                }
                
                # Add assignments to shift schedules
                for assignment in shift_assignments:
                    shift_id = assignment.get('shift_id')
                    emp_id = assignment.get('employee_id')
                    
                    # Handle special cases
                    if assignment.get('type') == 'absence':
                        shift_id = 99999  # Use special absence ID
                    elif assignment.get('type') == 'leave':
                        shift_id = 99998  # Use special leave ID
                    
                    if shift_id in shift_schedules:
                        shift_schedules[shift_id]['assignments'].append({
                            'date': assignment.get('date'),
                            'employee_id': emp_id,
                            'employee_name': employee_names.get(emp_id, f'Mitarbeiter {emp_id}')
                        })
                
                return {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'days_in_range': (end_date - start_date).days + 1,
                    'shifts': list(shift_schedules.values())
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating einsatzplan range: {str(e)}")

        @self.app.get("/schedule/jahresuebersicht")
        async def get_jahresuebersicht(
                employee_id: int = Query(..., description="Employee ID (required)"),
                year: int = Query(..., description="Year (required)")
        ):
            """Get Jahresübersicht view - yearly schedule matrix for a specific employee."""
            try:
                # Get employee info
                emp_result = self.engine.query().select("5EMPL").where("id", "=", employee_id).execute()
                employees = emp_result.to_dict()
                if not employees:
                    raise HTTPException(status_code=404, detail="Employee not found")
                employee = employees[0]
                
                # Create employee response object
                employee_response = {
                    "id": employee.get('id'),
                    "displayName": f"{employee.get('firstname', '')} {employee.get('name', '')}".strip(),
                    "firstName": employee.get('firstname', ''),
                    "lastName": employee.get('name', '')
                }
                
                # Get shift assignments for the year - COMBINE all relevant types
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
                
                # Get main assignments (5MASHI)
                mashi_query = self.engine.query().select("5MASHI").where("employee_id", "=", employee_id)
                mashi_result = mashi_query.execute()
                mashi_assignments = mashi_result.to_dict()
                
                # Get special assignments (5SPSHI) - these are manual/override assignments
                spshi_query = self.engine.query().select("5SPSHI").where("employee_id", "=", employee_id)
                spshi_result = spshi_query.execute()
                spshi_assignments = spshi_result.to_dict()
                
                # Get absences (5ABSEN)
                absences_query = self.engine.query().select("5ABSEN").where("employee_id", "=", employee_id)
                absences_result = absences_query.execute()
                absences_data = absences_result.to_dict()
                
                # Get leave data (if separate table exists) - try 5LEAVE or similar
                leave_assignments = []
                try:
                    leave_query = self.engine.query().select("5LEAVE").where("employee_id", "=", employee_id)
                    leave_result = leave_query.execute()
                    leave_assignments = leave_result.to_dict()
                except:
                    # 5LEAVE table might not exist or be named differently
                    pass
                
                print(f"DEBUG Jahresübersicht: Found {len(mashi_assignments)} main + {len(spshi_assignments)} special + {len(absences_data)} absences + {len(leave_assignments)} leaves for employee {employee_id}")
                
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Initialize months structure with German names
                german_months = [
                    "Jänner", "Februar", "März", "April", "Mai", "Juni",
                    "Juli", "August", "September", "Oktober", "November", "Dezember"
                ]
                
                months = []
                for month_num in range(1, 13):
                    import calendar
                    days_in_month = calendar.monthrange(year, month_num)[1]
                    
                    # Initialize all days to null
                    days = {}
                    for day in range(1, 32):
                        days[str(day)] = None
                    
                    months.append({
                        "month": month_num,
                        "name": german_months[month_num - 1],
                        "days": days
                    })
                
                # Process all assignment types, with priority: Leave > Absence > Special > Main
                all_assignments = []
                
                # Add main assignments with lowest priority
                for shift in mashi_assignments:
                    if shift.get('date'):
                        all_assignments.append({
                            'date': shift.get('date'),
                            'shift_id': shift.get('shift_id'),
                            'type': 'main',
                            'priority': 1
                        })
                
                # Add special assignments with higher priority (manual overrides)
                for shift in spshi_assignments:
                    if shift.get('date'):
                        all_assignments.append({
                            'date': shift.get('date'),
                            'shift_id': shift.get('shift_id'),
                            'type': 'special',
                            'priority': 2
                        })
                
                # Add absences with highest priority
                for absence in absences_data:
                    if absence.get('date'):
                        all_assignments.append({
                            'date': absence.get('date'),
                            'shift_id': None,
                            'type': 'absence',
                            'priority': 3,
                            'absence_type': absence.get('type', 0)
                        })
                
                # Add leave data with highest priority
                for leave in leave_assignments:
                    if leave.get('date'):
                        all_assignments.append({
                            'date': leave.get('date'),
                            'shift_id': None,
                            'type': 'leave',
                            'priority': 4,
                            'leave_type': leave.get('leave_type_id', 0)
                        })
                
                # Create a dictionary to track assignments by date for priority handling
                date_assignments = {}
                
                # Process assignments and apply to months structure
                for assignment in all_assignments:
                    assignment_date = assignment.get('date')
                    if assignment_date:
                        # Parse date
                        if isinstance(assignment_date, str):
                            try:
                                if '-' in assignment_date:
                                    parsed_date = datetime.strptime(assignment_date[:10], '%Y-%m-%d').date()
                                else:
                                    continue
                            except ValueError:
                                continue
                        elif hasattr(assignment_date, 'date'):
                            parsed_date = assignment_date.date()
                        elif hasattr(assignment_date, 'year'):
                            parsed_date = assignment_date
                        else:
                            continue
                        
                        # Only process dates within the requested year
                        if start_date <= parsed_date <= end_date:
                            date_key = parsed_date.isoformat()
                            
                            # Use priority to determine which assignment to keep
                            if date_key not in date_assignments or assignment['priority'] > date_assignments[date_key]['priority']:
                                date_assignments[date_key] = assignment
                
                # Apply the final assignments to the months structure
                for date_key, assignment in date_assignments.items():
                    parsed_date = datetime.strptime(date_key, '%Y-%m-%d').date()
                    month_idx = parsed_date.month - 1
                    day_str = str(parsed_date.day)
                    
                    if assignment['type'] == 'absence':
                        months[month_idx]['days'][day_str] = "AB"
                    elif assignment['type'] == 'leave':
                        months[month_idx]['days'][day_str] = "UA"
                    elif assignment['type'] in ['main', 'special']:
                        shift_def = shift_definitions.get(assignment.get('shift_id'), {})
                        # Use shortname if available, otherwise first 2 characters of name
                        shift_code = shift_def.get('shortname')
                        if not shift_code and shift_def.get('name'):
                            shift_code = shift_def['name'][:2].upper()
                        if not shift_code:
                            shift_code = "T"  # Default fallback
                        
                        # Use the actual shift code for both main and special assignments
                        # Special assignments automatically override main ones due to priority system
                        months[month_idx]['days'][day_str] = shift_code
                
                # Create legend
                legend = {
                    "SD": "Spätdienst",
                    "FD": "Frühdienst", 
                    "UA": "Urlaub",
                    "AB": "Abwesenheit",
                    "SP": "Special",
                    "MN": "Manual"
                }
                
                # Add shift-specific entries to legend based on available shift definitions
                for shift_def in shift_definitions.values():
                    shortname = shift_def.get('shortname')
                    if shortname and shortname not in legend:
                        legend[shortname] = shift_def.get('name', shortname)
                
                return {
                    "employee": employee_response,
                    "year": year,
                    "months": months,
                    "legend": legend
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating jahresübersicht: {str(e)}")


        # Export endpoint
        @self.app.post("/export")
        async def export_data(request: ExportRequest):
            """Export data in various formats."""
            # Build query
            query = self.engine.query().select(request.table)

            for filter in request.filters:
                query = query.where(filter.field, filter.operator, filter.value)

            result = query.execute()

            if not result.records:
                raise HTTPException(status_code=404, detail="No data to export")

            # Export
            data = result.to_dict()

            # Filter fields if specified
            if request.fields:
                data = [
                    {k: v for k, v in record.items() if k in request.fields}
                    for record in data
                ]

            # Export to bytes
            output = io.BytesIO()

            if request.format == "csv":
                content = self.exporter.to_csv(data)
                output.write(content.encode('utf-8'))
                media_type = "text/csv"
                filename = f"export_{request.table}.csv"
            elif request.format == "json":
                content = self.exporter.to_json(data)
                output.write(content.encode('utf-8'))
                media_type = "application/json"
                filename = f"export_{request.table}.json"
            elif request.format == "excel":
                content = self.exporter.to_excel(data)
                output.write(content)
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                filename = f"export_{request.table}.xlsx"
            else:
                content = self.exporter.to_html(data)
                output.write(content.encode('utf-8'))
                media_type = "text/html"
                filename = f"export_{request.table}.html"

            output.seek(0)

            return StreamingResponse(
                output,
                media_type=media_type,
                headers={
                    "Content-Disposition": f"attachment; filename={filename}"
                }
            )

        # Report endpoints
        @self.app.get("/reports/summary")
        async def get_reports_summary():
            """Get available reports and basic statistics."""
            try:
                # Get basic statistics
                employees_count = len(self.engine.loaded_tables.get("5EMPL", []))
                groups_count = len(self.engine.loaded_tables.get("5GROUP", []))
                shifts_count = len(self.engine.loaded_tables.get("5SHIFT", []))
                mashi_count = len(self.engine.loaded_tables.get("5MASHI", []))
                spshi_count = len(self.engine.loaded_tables.get("5SPSHI", []))
                
                # Get date range from shift data
                all_shifts = self.engine.loaded_tables.get("5MASHI", []) + self.engine.loaded_tables.get("5SPSHI", [])
                dates = []
                for shift in all_shifts:
                    if hasattr(shift, 'date') and shift.date:
                        try:
                            if isinstance(shift.date, str) and '-' in shift.date:
                                shift_date = datetime.strptime(shift.date[:10], '%Y-%m-%d').date()
                                dates.append(shift_date)
                        except:
                            continue
                
                date_range = {
                    "earliest": min(dates).isoformat() if dates else None,
                    "latest": max(dates).isoformat() if dates else None,
                    "total_shifts": len(dates)
                }
                
                return {
                    "available_reports": [
                        {
                            "id": "employee-shifts",
                            "name": "Mitarbeiter Schichtverteilung",
                            "description": "Zeigt die Schichtverteilung pro Mitarbeiter",
                            "parameters": ["employee_id", "year", "month"]
                        },
                        {
                            "id": "shift-coverage", 
                            "name": "Schichtbesetzung",
                            "description": "Zeigt die Besetzung verschiedener Schichttypen",
                            "parameters": ["year", "month"]
                        },
                        {
                            "id": "monthly-stats",
                            "name": "Monatsstatistik",
                            "description": "Statistische Auswertung für einen Monat",
                            "parameters": ["year", "month"]
                        },
                        {
                            "id": "employee-workload",
                            "name": "Arbeitsbelastung",
                            "description": "Arbeitsbelastung pro Mitarbeiter",
                            "parameters": ["year", "month"]
                        }
                    ],
                    "statistics": {
                        "employees": employees_count,
                        "groups": groups_count,
                        "shift_types": shifts_count,
                        "main_assignments": mashi_count,
                        "special_assignments": spshi_count,
                        "total_assignments": mashi_count + spshi_count,
                        "date_range": date_range
                    }
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating reports summary: {str(e)}")

        @self.app.get("/reports/employee-shifts/{employee_id}")
        async def get_employee_shifts_report(
            employee_id: int = Path(..., ge=1),
            year: int = Query(2025),
            month: Optional[int] = Query(None, ge=1, le=12)
        ):
            """Generate employee shift distribution report."""
            try:
                # Get employee info
                emp_result = self.engine.query().select("5EMPL").where("id", "=", employee_id).execute()
                employees = emp_result.to_dict()
                if not employees:
                    raise HTTPException(status_code=404, detail="Employee not found")
                employee = employees[0]
                
                # Get shift assignments
                mashi_query = self.engine.query().select("5MASHI").where("employee_id", "=", employee_id)
                mashi_result = mashi_query.execute()
                mashi_assignments = mashi_result.to_dict()
                
                spshi_query = self.engine.query().select("5SPSHI").where("employee_id", "=", employee_id)
                spshi_result = spshi_query.execute()
                spshi_assignments = spshi_result.to_dict()
                
                all_assignments = mashi_assignments + spshi_assignments
                
                # Get shift definitions
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                # Filter by date if specified
                filtered_assignments = []
                for assignment in all_assignments:
                    if assignment.get('date'):
                        try:
                            if isinstance(assignment['date'], str) and '-' in assignment['date']:
                                shift_date = datetime.strptime(assignment['date'][:10], '%Y-%m-%d').date()
                                if shift_date.year == year and (month is None or shift_date.month == month):
                                    filtered_assignments.append({
                                        **assignment,
                                        'parsed_date': shift_date,
                                        'shift_name': shift_definitions.get(assignment.get('shift_id'), {}).get('name', 'Unknown')
                                    })
                        except:
                            continue
                
                # Group by shift type
                shift_stats = {}
                for assignment in filtered_assignments:
                    shift_name = assignment['shift_name']
                    if shift_name not in shift_stats:
                        shift_stats[shift_name] = {
                            'count': 0,
                            'dates': []
                        }
                    shift_stats[shift_name]['count'] += 1
                    shift_stats[shift_name]['dates'].append(assignment['parsed_date'].isoformat())
                
                return {
                    "employee": employee,
                    "period": {
                        "year": year,
                        "month": month,
                        "month_name": ["", "Januar", "Februar", "März", "April", "Mai", "Juni", 
                                     "Juli", "August", "September", "Oktober", "November", "Dezember"][month] if month else "Ganzes Jahr"
                    },
                    "total_shifts": len(filtered_assignments),
                    "shift_distribution": shift_stats,
                    "assignments": filtered_assignments[:50]  # Limit for performance
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating employee shifts report: {str(e)}")

        @self.app.get("/reports/shift-coverage")
        async def get_shift_coverage_report(
            year: int = Query(2025),
            month: int = Query(6, ge=1, le=12)
        ):
            """Generate shift coverage report."""
            try:
                start_date = date(year, month, 1)
                import calendar
                last_day = calendar.monthrange(year, month)[1]
                end_date = date(year, month, last_day)
                
                # Get all shift assignments for the period
                mashi_query = self.engine.query().select("5MASHI")
                mashi_result = mashi_query.execute()
                mashi_assignments = mashi_result.to_dict()
                
                spshi_query = self.engine.query().select("5SPSHI")
                spshi_result = spshi_query.execute()
                spshi_assignments = spshi_result.to_dict()
                
                all_assignments = mashi_assignments + spshi_assignments
                
                # Get shift definitions and employees
                shift_defs_result = self.engine.query().select("5SHIFT").execute()
                shift_definitions = {s['id']: s for s in shift_defs_result.to_dict()}
                
                employees_result = self.engine.query().select("5EMPL").execute()
                employees = {emp['id']: emp for emp in employees_result.to_dict()}
                
                # Filter assignments for the period
                coverage_data = {}
                for assignment in all_assignments:
                    if assignment.get('date'):
                        try:
                            if isinstance(assignment['date'], str) and '-' in assignment['date']:
                                shift_date = datetime.strptime(assignment['date'][:10], '%Y-%m-%d').date()
                                if start_date <= shift_date <= end_date:
                                    shift_def = shift_definitions.get(assignment.get('shift_id'), {})
                                    shift_name = shift_def.get('name', 'Unknown')
                                    employee = employees.get(assignment.get('employee_id'), {})
                                    
                                    day = shift_date.day
                                    if shift_name not in coverage_data:
                                        coverage_data[shift_name] = {
                                            'shift_definition': shift_def,
                                            'coverage_by_day': {},
                                            'total_assignments': 0
                                        }
                                    
                                    if day not in coverage_data[shift_name]['coverage_by_day']:
                                        coverage_data[shift_name]['coverage_by_day'][day] = []
                                    
                                    coverage_data[shift_name]['coverage_by_day'][day].append({
                                        'employee_id': assignment.get('employee_id'),
                                        'employee_name': f"{employee.get('name', 'Unknown')} {employee.get('firstname', '')}".strip()
                                    })
                                    coverage_data[shift_name]['total_assignments'] += 1
                        except:
                            continue
                
                # Calculate coverage statistics
                coverage_stats = {}
                for shift_name, data in coverage_data.items():
                    days_covered = len(data['coverage_by_day'])
                    total_people = sum(len(people) for people in data['coverage_by_day'].values())
                    avg_people_per_day = total_people / days_covered if days_covered > 0 else 0
                    
                    coverage_stats[shift_name] = {
                        'days_covered': days_covered,
                        'days_in_month': last_day,
                        'coverage_percentage': (days_covered / last_day) * 100,
                        'total_assignments': data['total_assignments'],
                        'avg_people_per_day': round(avg_people_per_day, 1),
                        'coverage_by_day': data['coverage_by_day']
                    }
                
                return {
                    "period": {
                        "year": year,
                        "month": month,
                        "month_name": ["", "Januar", "Februar", "März", "April", "Mai", "Juni", 
                                     "Juli", "August", "September", "Oktober", "November", "Dezember"][month],
                        "days_in_month": last_day
                    },
                    "coverage_statistics": coverage_stats,
                    "summary": {
                        "total_shift_types": len(coverage_stats),
                        "best_covered_shift": max(coverage_stats.items(), key=lambda x: x[1]['coverage_percentage'])[0] if coverage_stats else None,
                        "worst_covered_shift": min(coverage_stats.items(), key=lambda x: x[1]['coverage_percentage'])[0] if coverage_stats else None
                    }
                }
                
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error generating shift coverage report: {str(e)}")

        @self.app.post("/reports")
        async def generate_report(request: ReportRequest):
            """Generate various reports (legacy endpoint)."""
            try:
                if request.report_type == "absence":
                    if self.report_generator:
                        report = self.report_generator.employee_absence_report(
                            request.parameters.get("employee_id"),
                            request.parameters.get("year", datetime.now().year)
                        )
                        return report.to_dict()
                    else:
                        return {"error": "Report generator not available", "message": "This feature requires the full report generator"}
                        
                elif request.report_type == "shifts":
                    # Redirect to new endpoint
                    employee_id = request.parameters.get("employee_id")
                    year = request.parameters.get("year", datetime.now().year)
                    month = request.parameters.get("month")
                    
                    if employee_id:
                        # Use employee shifts report
                        return await get_employee_shifts_report(employee_id, year, month)
                    else:
                        # Use shift coverage report
                        return await get_shift_coverage_report(year, month or 6)
                        
                else:
                    return {
                        "error": f"Report type '{request.report_type}' not implemented",
                        "available_reports": ["absence", "shifts"],
                        "message": "Use /reports/summary to see all available reports"
                    }

            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))

        # Validation endpoint
        @self.app.post("/validate")
        async def validate_data():
            """Validate data integrity."""
            report = self.validator.validate_all_tables(self.engine.loaded_tables)

            return {
                "valid": not report.has_errors(),
                "errors": len(report.errors),
                "warnings": len(report.warnings),
                "details": {
                    "errors": [str(e) for e in report.errors[:10]],
                    "warnings": [str(w) for w in report.warnings[:10]],
                    "statistics": report.statistics
                }
            }

        # Performance stats
        @self.app.get("/stats/performance")
        async def get_performance_stats():
            """Get performance statistics."""
            return performance_monitor.get_statistics()

        # Search endpoint
        @self.app.get("/search/employees")
        async def search_employees(
                q: str = Query(..., min_length=2),
                limit: int = Query(20, ge=1, le=100)
        ):
            """Search employees by name or other fields."""
            results = self.engine.search_employees(q)
            return results[:limit]

        # Include advanced analytics router
        from .advanced_analytics import create_advanced_analytics_router
        advanced_router = create_advanced_analytics_router(self.engine)
        self.app.include_router(advanced_router)


def create_api(dbf_dir: FilePath, **kwargs) -> FastAPI:
    """Create and return the FastAPI application."""
    api = SchichtplanerAPI(dbf_dir, **kwargs)
    return api.app


# Standalone runner
if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Run Schichtplaner5 API server")
    parser.add_argument("--dir", required=True, help="DBF directory path")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    app = create_api(FilePath(args.dir))

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload
    )