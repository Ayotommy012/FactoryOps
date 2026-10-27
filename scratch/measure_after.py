import time
import json
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000"

def do_req(method, url, data=None):
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            return resp.status, json.loads(content.decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")

def measure(name, fn):
    t0 = time.perf_counter()
    status, res = fn()
    dt = (time.perf_counter() - t0) * 1000.0
    print(f"[{name}] {dt:.1f} ms (status: {status})")
    return (status, res), dt

print("=== MEASURING POST-FIX LATENCY ===")

# 1. Loading scenario list
_, dt_list = measure("GET /simulations", lambda: do_req("GET", f"{BASE_URL}/simulations"))

# 2. Create disposable scenario
disposable_id = f"scn_clock_post_{int(time.time())}"
payload = {
    "name": "Clock Post-Fix Disposable Scenario",
    "disruption_type": "roaster_bearing_overheat",
    "random_seed": 42,
    "step_size_minutes": 15,
    "scenario_id": disposable_id,
    "autonomous_recovery": False,
}
_, dt_create = measure("POST /simulations (create)", lambda: do_req("POST", f"{BASE_URL}/simulations", payload))

# 3. Fetch selected scenario
_, dt_get = measure(f"GET /simulations/{disposable_id}", lambda: do_req("GET", f"{BASE_URL}/simulations/{disposable_id}"))

# 4. Fetch timing status
_, dt_timing = measure(f"GET /simulations/{disposable_id}/timing", lambda: do_req("GET", f"{BASE_URL}/simulations/{disposable_id}/timing"))

# 5. Fetch timeline
_, dt_tl = measure(f"GET /simulations/{disposable_id}/timeline", lambda: do_req("GET", f"{BASE_URL}/simulations/{disposable_id}/timeline"))

# 6. Fetch world model
_, dt_wm = measure(f"GET /simulations/{disposable_id}/world-model", lambda: do_req("GET", f"{BASE_URL}/simulations/{disposable_id}/world-model"))

# 7. Fetch recovery impact
_, dt_imp = measure(f"GET /simulations/{disposable_id}/recovery/impact", lambda: do_req("GET", f"{BASE_URL}/simulations/{disposable_id}/recovery/impact"))

# 8. Step scenario
_, dt_step1 = measure(f"POST /simulations/{disposable_id}/step (1)", lambda: do_req("POST", f"{BASE_URL}/simulations/{disposable_id}/step", {"steps": 1}))
_, dt_step2 = measure(f"POST /simulations/{disposable_id}/step (2)", lambda: do_req("POST", f"{BASE_URL}/simulations/{disposable_id}/step", {"steps": 1}))
_, dt_step3 = measure(f"POST /simulations/{disposable_id}/step (3)", lambda: do_req("POST", f"{BASE_URL}/simulations/{disposable_id}/step", {"steps": 1}))

# 9. Request recovery plans
_, dt_plans = measure(f"POST /simulations/{disposable_id}/recovery/plans", lambda: do_req("POST", f"{BASE_URL}/simulations/{disposable_id}/recovery/plans", {}))

print("\nSummary of post-fix latencies (ms):")
print(f"  List simulations: {dt_list:.1f} ms")
print(f"  Create simulation: {dt_create:.1f} ms")
print(f"  Get simulation: {dt_get:.1f} ms")
print(f"  Get timing: {dt_timing:.1f} ms")
print(f"  Get timeline: {dt_tl:.1f} ms")
print(f"  Get world-model: {dt_wm:.1f} ms")
print(f"  Get impact: {dt_imp:.1f} ms")
print(f"  Step 1: {dt_step1:.1f} ms")
print(f"  Step 2: {dt_step2:.1f} ms")
print(f"  Step 3: {dt_step3:.1f} ms")
print(f"  Fetch plans: {dt_plans:.1f} ms")
