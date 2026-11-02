# Payment path notes (Day 1-2 spike)

Question: can an agent-initiated purchase complete in the PayPal sandbox without a
human redirect, or does every order need payer approval?

## Day 1 result (scripts/paypal_spike.py)
- Order created: yes / no
- Approval needed before capture: yes / no
- Capture before approval returned: (paste error name, e.g. ORDER_NOT_APPROVED)
- Capture after approval returned: (status and capture id)

## Day 2 options to test
- [ ] Agent Toolkit / MCP pay-order tool: does it pay without a redirect?
- [ ] Vaulted payment method for a merchant-initiated charge
- [ ] Fallback: create order, send human to approval link, then capture

## Decision
(write the chosen path here)
