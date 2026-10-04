import http from 'k6/http';
import { check, sleep } from 'k6';

const base = __ENV.BASE_URL || 'http://localhost:8000';
const username = __ENV.USERNAME || 'viewer';
const password = __ENV.PASSWORD || 'viewer123';
export const options = { thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<1000'] }, vus: 5, duration: '30s' };
export default function () {
  const login = http.post(`${base}/auth/login`, JSON.stringify({ username, password }), { headers: { 'Content-Type': 'application/json' } });
  check(login, { 'login succeeds': (r) => r.status === 200 });
  const token = login.json('access_token');
  const headers = { Authorization: `Bearer ${token}` };
  const incidents = http.get(`${base}/incidents`, { headers });
  check(incidents, { 'incidents succeeds': (r) => r.status === 200 });
  sleep(1);
}
