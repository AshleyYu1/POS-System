# Pantry — local F&B POS

Run `./start.ps1` in PowerShell, then open **http://127.0.0.1:8000**. Keep the terminal open; Ctrl+C stops the app. Python 3.10+ is required. No packages need installing.

## First use

1. Inventory → Add. Set name, Food or Non-food type, fixed unit, opening stock, optional default cost per unit and low-stock threshold. Cups, lids and other non-food supplies can also be attached to menu recipes. No SKU entry is needed; internal identifiers are generated automatically and existing identifiers are preserved. Optional expiry dates belong to individual stock lots.
2. Add a composite ingredient after its components exist. Select ingredients, their quantities, and the recipe output amount. Saving a recipe does not alter inventory.
3. Prepare batch → enter the output amount → preview → confirm. Component stock decreases and prepared stock increases together. Nested composites use their already-prepared stock.
4. Menu items → add a name, category, price and inventory quantities per serving. Availability can be toggled. There is no search.
5. Checkout → choose a category and menu items → adjust the cart → enter payment method and cash received → complete sale. Sales require sufficient ingredient stock. View or print the receipt.
6. Use Stock entry for replenishment, waste and corrections. Each needs a reason. Physical counts can record a comparison only or correct stock explicitly.
7. Edit stock in Inventory sets a new total quantity and records the difference. Reason is optional, with Restocking, Wastage and Count correction suggestions. A changed stock balance requires reopening the form to prevent stale edits. Low-stock alerts appear on Dashboard and Inventory, not Checkout.

## Dashboard and reports

The app opens on the café dashboard. Choose Daily and a date, or Weekly and any date in the desired Monday–Sunday week. Dates and hourly buckets use Asia/Manila time. Revenue, completed orders, average order, servings, payment breakdown, bestsellers and a sales chart are generated from saved sale snapshots. Low-stock alerts show current inventory regardless of report date.

Voided sales are excluded from revenue for their original sale period and shown separately. Reports therefore reflect current void status rather than an immutable end-of-day closure. Export report downloads a CSV with the totals, payment breakdown, time buckets and all sold items. There is no scheduled branch report delivery yet.

## Revenue, COGS and gross profit

Enter inventory cost in PHP per one stock unit (e.g. PHP 0.10 per mL). A default cost applies to newly added stock; Stock entry can override it for a delivery. Each lot preserves its acquisition cost. Prepared items inherit the total cost of the component lots consumed, divided by the output quantity. Sales preserve the cost of the lots they actually consume, including packaging. Later recipe or cost edits do not rewrite prior sale costs. COGS is rounded to centavos per sale.

The dashboard and CSV show revenue, COGS and **gross profit = revenue − COGS**. Gross profit excludes wages, rent, other overhead and inventory waste not associated with a sale. It is not net profit. Voided sales are excluded from both revenue and COGS.

Costs omitted for old lots or sales remain unknown; they are not assumed to be zero. If any completed sale in a period lacks cost data, that period shows COGS/profit as unavailable and the number of affected orders. Existing stock costs and historical orders are not retroactively estimated from a newly entered default cost. Explicit zero cost is supported.

## Records and safeguards

- Data persists in `data/pos.sqlite3`. Back up this file with the app stopped. There is no cloud synchronization.
- Sales snapshot names, categories, prices and recipes. Preparations snapshot consumed quantities and output. Later edits do not rewrite these records.
- Voids require a reason, preserve the sale, and restore its original ingredient allocations and lot expiry dates once. If food cannot be reused, record waste after the reversal.
- Stock is consumed from the earliest dated lot first, then undated lots. Expired lots are displayed as expired; they are **not blocked automatically**. Staff must record waste for discarded stock.
- Quantities use the item's fixed unit. No automatic unit conversion. Decimal quantities are supported. Sale quantities are whole servings. Recipe scaling assumes expected yield; actual-yield/loss entry is deferred.
- Physical-count input shows current system stock after an item is selected. Comparison at save time uses the then-current stock. This is a local workflow aid, not independent staff access control.
- SQLite transactions ensure stock mutations and their associated records commit together or roll back together. Currency is stored in integer centavos.

## Current scope

Single local workstation, no login or staff permissions. No branch synchronization, delivery purchasing workflow, scheduled branch end-of-day reports, lead-time recommendations, taxes, discounts, refunds or tax-compliant invoicing. The receipt is an internal transaction receipt. Daily/weekly sales reports, sales history, low-stock alerts, expiry lots, movements and preparation/count records are included.

## Verification

Run `& "$env:LOCALAPPDATA/Programs/Python/Python313/python.exe" -m unittest -v`, `node --check static/app.js` and `node test_reports.js`. Database tests use a temporary database, never the live records. Report checks cover Philippine timezone boundaries, Monday–Sunday weeks, voids, payments, historical prices and empty periods.
