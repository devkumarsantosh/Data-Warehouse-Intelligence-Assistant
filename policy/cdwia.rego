package cdwia

import future.keywords.in

# Default deny — every request must be explicitly allowed.
default allow = false

# Analysts and admins can query within their own business unit.
allow {
    input.action == "query"
    some role in input.roles
    role in {"analyst", "admin"}
    input.business_unit != ""
}

# Admins may also query across business units within their tenant.
allow {
    input.action == "query"
    "admin" in input.roles
}
