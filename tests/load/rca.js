import http from 'k6/http';
import { check } from 'k6';
const base = __ENV.RCA_URL || 'http://localhost:8004';
export const options = { thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<1500'] }, vus: 2, duration: '30s' };
export default function () {
  const response = http.get(`${base}/health`);
  check(response, { 'RCA is healthy': (r) => r.status === 200 });
}
