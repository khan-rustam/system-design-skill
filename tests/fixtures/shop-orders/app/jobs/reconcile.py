import logging

from ..db import execute

log = logging.getLogger(__name__)

RECONCILE_SQL = """
UPDATE products p
SET sold_count = p.sold_count + s.total_sold
FROM (
    SELECT product_id, SUM(quantity) AS total_sold
    FROM orders
    WHERE status = 'paid'
    GROUP BY product_id
) s
WHERE s.product_id = p.id
"""


def reconcile_sales_counts():
    log.info("reconciling product sales counts")
    execute(RECONCILE_SQL)
    log.info("sales counts reconciled")
