# openschichtplaner5-api

[![FastAPI](https://img.shields.io/badge/FastAPI-0.68+-green.svg)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

REST API server for OpenSchichtplaner5 - provides comprehensive HTTP endpoints for accessing Schichtplaner5 shift planning data.

## 🎯 Overview

The openschichtplaner5-api provides a modern, async REST API built with FastAPI that exposes all Schichtplaner5 data through well-designed HTTP endpoints. It includes comprehensive analytics, data export capabilities, and automatic OpenAPI documentation.

## 🚀 Quick Start

```bash
# Run the API server directly
cd openschichtplaner5-api
python -m openschichtplaner5_api --dir /path/to/dbf/files --port 8080

# Or use the main web server (recommended)
cd ../openschichtplaner5-webserver
python -m openschichtplaner5_webserver.main --dir /path/to/dbf/files --port 8080
```

Access the API:
- **Interactive Docs**: http://localhost:8080/api/docs
- **OpenAPI Spec**: http://localhost:8080/api/openapi.json
- **Health Check**: http://localhost:8080/api/health

## 📡 API Endpoints

### Core Data Endpoints
- `GET /api/employees` - List all employees
- `GET /api/employees/{id}` - Get specific employee
- `GET /api/groups` - List organizational groups
- `GET /api/shifts` - List shift definitions
- `GET /api/workplaces` - List work locations

### Schedule Management
- `GET /api/schedule/dienstplan` - Employee-based schedule view
- `GET /api/schedule/einsatzplan` - Shift-based schedule view
- `GET /api/schedule/dienstplan-range` - Schedule for date range
- `GET /api/assignments/{employee_id}` - Employee's shift assignments

### Leave Management
- `GET /api/absences` - Employee absences
- `GET /api/leave-types` - Available leave types
- `GET /api/leave-entitlements` - Annual leave entitlements

### Analytics Endpoints
- `GET /api/analytics/workforce-intelligence` - Comprehensive workforce analysis
- `GET /api/analytics/capacity-planning` - Staffing capacity analysis
- `GET /api/analytics/employee-utilization` - Employee utilization metrics
- `GET /api/analytics/shift-coverage` - Shift coverage analysis

### Data Export
- `GET /api/export/employees/{format}` - Export employee data
- `GET /api/export/schedule/{format}` - Export schedule data
- `GET /api/export/analytics/{format}` - Export analytics reports

## 🔧 Features

### FastAPI Benefits
- **Automatic Documentation**: Interactive Swagger UI and ReDoc
- **Type Safety**: Pydantic models for request/response validation
- **Async Support**: High-performance async/await architecture
- **OpenAPI Standard**: Full OpenAPI 3.0 specification

### Advanced Analytics
- **Workforce Intelligence**: Employee utilization, capacity planning
- **Predictive Analytics**: Staffing forecasts and trend analysis
- **Financial Analytics**: Cost analysis and budget optimization
- **Operational Insights**: Schedule efficiency metrics

## 📊 API Usage Examples

### Get Employee Data
```python
import requests

# Get all employees
response = requests.get("http://localhost:8080/api/employees")
employees = response.json()

# Get specific employee
response = requests.get("http://localhost:8080/api/employees/123")
employee = response.json()
```

### Schedule Queries
```python
# Get schedule for date range
response = requests.get(
    "http://localhost:8080/api/schedule/dienstplan-range",
    params={
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
        "group_id": 5
    }
)
schedule = response.json()
```

### Analytics Data
```python
# Get workforce intelligence
response = requests.get("http://localhost:8080/api/analytics/workforce-intelligence")
analytics = response.json()

print(f"Total employees: {analytics['total_employees']}")
print(f"Average utilization: {analytics['avg_utilization']:.1%}")
```

⚠️ **Security Note**: The current API has no authentication. For production use, implement JWT tokens, CORS restrictions, and role-based access control.

## 📄 License

This API server is part of the OpenSchichtplaner5 project and is licensed under the MIT License.
