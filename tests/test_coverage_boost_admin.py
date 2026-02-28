"""Tests targeting uncovered lines in admin.py to boost coverage."""
import io
import zipfile
import pytest
from starlette.testclient import TestClient


class TestPeriods:
    """Test accounting period CRUD."""

    def test_get_periods_with_group_id(self, sync_client: TestClient):
        """GET /api/periods?group_id=1 → 200."""
        res = sync_client.get('/api/periods?group_id=1')
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_create_period_invalid_date(self, planer_client: TestClient):
        """POST /api/periods with invalid date → 400."""
        res = planer_client.post('/api/periods', json={
            'group_id': 1,
            'start': 'not-a-date',
            'end': '2024-12-31',
        })
        assert res.status_code == 400

    def test_create_period_end_before_start(self, planer_client: TestClient):
        """POST /api/periods with end < start → 400."""
        res = planer_client.post('/api/periods', json={
            'group_id': 1,
            'start': '2024-12-01',
            'end': '2024-01-01',
        })
        assert res.status_code == 400

    def test_create_and_delete_period(self, planer_client: TestClient):
        """POST then DELETE /api/periods → both 200."""
        # Get a valid group_id first
        groups = planer_client.get('/api/groups').json()
        group_id = groups[0]['ID'] if groups else 1
        res = planer_client.post('/api/periods', json={
            'group_id': group_id,
            'start': '2024-01-01',
            'end': '2024-03-31',
            'description': 'Test Periode',
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True
        period_id = res.json()['record']['id']  # lowercase 'id'

        del_res = planer_client.delete(f'/api/periods/{period_id}')
        assert del_res.status_code == 200
        assert del_res.json()['ok'] is True


class TestSettings:
    """Test settings read/write."""

    def test_get_settings(self, sync_client: TestClient):
        """GET /api/settings → 200."""
        res = sync_client.get('/api/settings')
        assert res.status_code == 200

    def test_update_settings(self, admin_client: TestClient):
        """PUT /api/settings → 200."""
        res = admin_client.put('/api/settings', json={'BACKUPFR': 7})
        assert res.status_code == 200
        assert res.json()['ok'] is True


class TestBackups:
    """Test backup download/list/restore."""

    def test_backup_download(self, admin_client: TestClient):
        """GET /api/backup/download → 200, returns zip."""
        res = admin_client.get('/api/backup/download')
        assert res.status_code == 200
        assert res.headers.get('content-type', '').startswith('application/zip')

    def test_list_backups(self, admin_client: TestClient):
        """GET /api/admin/backups → 200."""
        res = admin_client.get('/api/admin/backups')
        assert res.status_code == 200
        data = res.json()
        assert 'backups' in data

    def test_download_saved_backup_invalid_filename(self, admin_client: TestClient):
        """GET /api/admin/backups/{bad_name}/download → 400."""
        res = admin_client.get('/api/admin/backups/evil_file.zip/download')
        assert res.status_code == 400

    def test_download_saved_backup_invalid_name2(self, admin_client: TestClient):
        """GET with non-sp5_backup_ filename → 400."""
        res = admin_client.get('/api/admin/backups/evil.zip/download')
        assert res.status_code == 400

    def test_download_saved_backup_not_found(self, admin_client: TestClient):
        """GET /api/admin/backups/sp5_backup_99991231_000000.zip/download → 404 or 500."""
        res = admin_client.get('/api/admin/backups/sp5_backup_99991231_000000.zip/download')
        assert res.status_code in (404, 500)

    def test_delete_saved_backup_invalid_name(self, admin_client: TestClient):
        """DELETE with invalid filename → 400."""
        res = admin_client.delete('/api/admin/backups/notabackup.zip')
        assert res.status_code == 400

    def test_delete_saved_backup_not_found(self, admin_client: TestClient):
        """DELETE nonexistent backup → 404 or 500."""
        res = admin_client.delete('/api/admin/backups/sp5_backup_99991231_000000.zip')
        assert res.status_code in (404, 500)

    def test_backup_restore_bad_zip(self, admin_client: TestClient):
        """POST /api/backup/restore with invalid ZIP → 400."""
        res = admin_client.post(
            '/api/backup/restore',
            files={'file': ('backup.zip', io.BytesIO(b'not a zip'), 'application/zip')},
        )
        assert res.status_code == 400

    def test_backup_restore_no_dbf(self, admin_client: TestClient):
        """POST /api/backup/restore with ZIP containing no DBF → 400."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('readme.txt', 'hello')
        buf.seek(0)
        res = admin_client.post(
            '/api/backup/restore',
            files={'file': ('backup.zip', buf, 'application/zip')},
        )
        assert res.status_code == 400

    def test_backup_restore_success(self, admin_client: TestClient):
        """POST /api/backup/restore with valid ZIP containing DBF → 200."""
        # First download current backup
        dl = admin_client.get('/api/backup/download')
        assert dl.status_code == 200
        # Use that backup to restore
        res = admin_client.post(
            '/api/backup/restore',
            files={'file': ('backup.zip', io.BytesIO(dl.content), 'application/zip')},
        )
        assert res.status_code == 200
        data = res.json()
        assert 'restored' in data
        assert data['restored'] > 0

    def test_backup_restore_too_large(self, admin_client: TestClient):
        """POST /api/backup/restore with >50MB → 413."""
        big = b'\x00' * (51 * 1024 * 1024)
        res = admin_client.post(
            '/api/backup/restore',
            files={'file': ('big.zip', io.BytesIO(big), 'application/zip')},
        )
        assert res.status_code == 413

    def test_list_and_download_created_backup(self, admin_client: TestClient):
        """After backup download, backup appears in list."""
        # Trigger backup creation
        admin_client.get('/api/backup/download')
        # List backups
        res = admin_client.get('/api/admin/backups')
        data = res.json()
        backups = data.get('backups', [])
        if not backups:
            pytest.skip("No backups available (backup_dir not configured)")
        # Try downloading the first one
        fname = backups[0]['filename']
        dl_res = admin_client.get(f'/api/admin/backups/{fname}/download')
        assert dl_res.status_code == 200

    def test_delete_created_backup(self, admin_client: TestClient):
        """After backup creation, delete it."""
        admin_client.get('/api/backup/download')
        res = admin_client.get('/api/admin/backups')
        backups = res.json().get('backups', [])
        if not backups:
            pytest.skip("No backups available")
        fname = backups[0]['filename']
        del_res = admin_client.delete(f'/api/admin/backups/{fname}')
        assert del_res.status_code == 200


class TestCompactDatabase:
    """Test database compact endpoint."""

    def test_compact_database(self, admin_client: TestClient):
        """POST /api/admin/compact → 200."""
        res = admin_client.post('/api/admin/compact')
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True
        assert 'files_processed' in data
        assert 'total_records_removed' in data


class TestFrontendErrors:
    """Test frontend error reporting."""

    def test_report_frontend_error(self, sync_client: TestClient):
        """POST /api/errors → 200."""
        res = sync_client.post('/api/errors', json={
            'error': 'Test error',
            'url': 'https://example.com',
            'user_agent': 'test-agent',
        })
        assert res.status_code == 200
        assert res.json()['ok'] is True

    def test_get_frontend_errors(self, admin_client: TestClient):
        """GET /api/admin/frontend-errors → 200."""
        res = admin_client.get('/api/admin/frontend-errors')
        assert res.status_code == 200
        data = res.json()
        assert 'count' in data
        assert 'errors' in data


class TestCacheStats:
    """Test cache stats endpoint."""

    def test_get_cache_stats(self, admin_client: TestClient):
        """GET /api/admin/cache-stats → 200."""
        res = admin_client.get('/api/admin/cache-stats')
        assert res.status_code == 200
        data = res.json()
        assert data['ok'] is True
        assert 'cache' in data
