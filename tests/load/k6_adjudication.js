/**
 * k6 Load Test — NCPDP D.0 Adjudication Pipeline
 * Target: 10,000 concurrent claims, p99 < 200ms
 *
 * Run: k6 run tests/load/k6_adjudication.js
 * With options: k6 run --vus 200 --duration 60s tests/load/k6_adjudication.js
 */
import http from 'k6/http'
import { check, sleep } from 'k6'
import { Rate, Trend } from 'k6/metrics'

// Custom metrics
const adjudicationTime = new Trend('adjudication_response_ms')
const errorRate        = new Rate('adjudication_errors')
const slaViolations    = new Rate('sla_violations_200ms')

// Test configuration
export const options = {
  stages: [
    { duration: '30s',  target: 50   },   // Ramp up
    { duration: '60s',  target: 200  },   // Sustained load
    { duration: '120s', target: 500  },   // Peak load
    { duration: '60s',  target: 1000 },   // Stress test
    { duration: '30s',  target: 0    },   // Ramp down
  ],
  thresholds: {
    // Primary SLA: 99% of adjudication requests < 200ms
    'adjudication_response_ms{type:adjudication}': ['p(99)<200'],
    // Secondary: 95% < 150ms
    'adjudication_response_ms{type:adjudication}': ['p(95)<150'],
    // Error rate < 0.1%
    'adjudication_errors': ['rate<0.001'],
    // SLA violation rate < 1%
    'sla_violations_200ms': ['rate<0.01'],
    // HTTP error rate
    'http_req_failed': ['rate<0.005'],
  },
}

const BASE_URL = __ENV.API_URL || 'http://localhost:8001'

// Realistic BIN/PCN combinations for load testing
const TEST_PLANS = [
  { bin: '004336', pcn: 'ADV',    member: 'TEST001', name: 'Express Scripts' },
  { bin: '610415', pcn: 'ARGUS',  member: 'TEST002', name: 'Argus' },
  { bin: '610494', pcn: 'MARK',   member: 'TEST003', name: 'CVS Caremark' },
  { bin: '003858', pcn: 'MEDCO',  member: 'TEST004', name: 'Medco' },
  { bin: '015581', pcn: 'PRIME',  member: 'TEST005', name: 'Prime Therapeutics' },
]

// Common test NDCs (all fictitious for load testing)
const TEST_NDCS = [
  '00071015423',  // Atorvastatin 40mg (simulated)
  '00185064001',  // Metformin 500mg
  '00555097202',  // Lisinopril 10mg
  '00228296311',  // Amlodipine 5mg
  '16590095060',  // Omeprazole 20mg
]

let authToken = ''

export function setup() {
  // Authenticate once
  const loginRes = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ username: 'pharmacist', password: 'Pharmacist1234!' }),
    { headers: { 'Content-Type': 'application/json' } }
  )
  if (loginRes.status === 200) {
    const body = JSON.parse(loginRes.body)
    return { token: body.access_token }
  }
  console.error('Auth failed in setup:', loginRes.status)
  return { token: '' }
}

export default function (data) {
  const token = data.token
  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  }

  // Select random plan and NDC
  const plan = TEST_PLANS[Math.floor(Math.random() * TEST_PLANS.length)]
  const ndc  = TEST_NDCS[Math.floor(Math.random() * TEST_NDCS.length)]

  // ── Test 1: Adjudication claim submission ──────────────────────────
  const claimPayload = {
    fill_id:           '00000000-0000-0000-0000-000000000001',
    insurance_id:      '00000000-0000-0000-0000-000000000002',
    ingredient_cost:   Math.random() * 100 + 5,
    dispensing_fee:    1.50,
    usual_and_customary: Math.random() * 150 + 10,
  }

  const claimStart = Date.now()
  const claimRes = http.post(
    `${BASE_URL}/api/v1/claims/submit`,
    JSON.stringify(claimPayload),
    { headers, tags: { type: 'adjudication' } }
  )
  const claimDuration = Date.now() - claimStart

  adjudicationTime.add(claimDuration, { type: 'adjudication', plan: plan.name })
  errorRate.add(claimRes.status >= 500)
  slaViolations.add(claimDuration > 200)

  check(claimRes, {
    'adjudication returned 2xx':   (r) => r.status >= 200 && r.status < 300,
    'adjudication has status field': (r) => {
      try { return JSON.parse(r.body).status !== undefined } catch { return false }
    },
  })

  // ── Test 2: Health check baseline (should always be <5ms) ──────────
  const healthRes = http.get(`${BASE_URL}/health`, { tags: { type: 'health' } })
  check(healthRes, { 'health ok': (r) => r.status === 200 })

  // ── Test 3: Rx queue fetch (pharmacist workstation) ────────────────
  const queueRes = http.get(
    `${BASE_URL}/api/v1/prescriptions?limit=20`,
    { headers, tags: { type: 'queue' } }
  )
  check(queueRes, { 'queue fetch 200': (r) => r.status === 200 })

  sleep(0.05) // 50ms think time between virtual user requests
}

export function teardown(data) {
  console.log(`Load test complete.`)
  console.log(`Target SLA: adjudication p99 < 200ms`)
}
