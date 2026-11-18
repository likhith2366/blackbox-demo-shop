"""Send one order through checkout-svc and print the response."""
import json
import urllib.request

ORDER = {"order_id": "smoke-0001", "customer_id": 1, "item": "sku-1001", "qty": 1, "amount": 9.99}

req = urllib.request.Request("http://127.0.0.1:18101/orders", data=json.dumps(ORDER).encode(),
                             headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=5) as resp:
    print(resp.status, resp.read().decode())
