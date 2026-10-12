"""Expected structure of each raw source (from docs/02_as_is_architecture.md)."""

CRM_ACCOUNTS = ["Account ID", "Company Name", "Sector", "Account Manager", "Customer Since", "Status",
                "Closed Date"]
CRM_LINES = ["Line ID", "Account ID", "Business Line", "Annual Value (GBP)", "Contract Start",
             "Term (Years)", "Renewal Date", "End Date"]
HELPDESK = ["ticket_id", "opened_at", "first_responded_at", "closed_at", "priority", "category",
            "customer_name", "agent", "description", "resolution_notes", "kb_article_id"]
TELECOMS_CONTRACTS = ["contract_ref", "account_no", "account_name", "service_type", "monthly_charge",
                      "start_date", "end_date"]
TELECOMS_BILLS = ["bill_id", "account_no", "bill_month", "bill_date", "line_rental", "call_charges", "total"]
SHOP_ORDER = ["order_id", "customer_id", "company_name", "order_date", "status", "promised_date",
              "delivered_date", "lines", "order_total"]
SHOP_LINE = ["sku", "product", "quantity", "unit_price", "line_total"]
KB_FIELDS = ["id", "title", "type", "topic_ids", "author", "created", "updated", "status", "superseded_by"]
KB_OPTIONAL = ["superseded_on", "synthetic"]
