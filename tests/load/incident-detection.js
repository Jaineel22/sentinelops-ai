import http from 'k6/http';
import { check } from 'k6';
const base = __ENV.ORDERS_URL || 'http://localhost:8001';
export const options = { thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<500'] }, vus: 5, duration: '30s' };
export default function () {
  const response = http.post(`${base}/orders`, JSON.stringify({ amount: 42.5, currency: 'USD' }), { headers: { 'Content-Type': 'application/json' } });
  check(response, { 'telemetry accepted': (r) => r.status < 500 });
}
