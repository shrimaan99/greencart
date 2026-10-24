# =====================================================================
#  restock.py  -  the admin side of sellers and restocking
#
#  The admin can NOT add stock by hand. Stock only goes up when a purchase
#  order from a seller is received (trigger in 10_seller_module.sql).
# =====================================================================
from flask import render_template, request, redirect, url_for, flash, abort
import db
from helpers import admin_required, to_int, to_decimal, friendly

IN_PROGRESS = "('Open', 'Offered', 'Requested', 'Accepted', 'Shipped')"
PO_STATUSES = ["Open", "Offered", "Requested", "Accepted", "Shipped", "Received", "Declined", "Cancelled"]


def init(app):

    # -------------------------------------------------------------- sellers
    @app.route("/admin/sellers")
    @admin_required
    def admin_sellers():
        sellers = db.query_all(
            "SELECT * FROM vw_seller_summary "
            "ORDER BY FIELD(status, 'Pending', 'Approved', 'Suspended'), business_name")
        return render_template("admin_sellers.html", sellers=sellers)

    @app.route("/admin/sellers/<int:seller_id>/status", methods=["POST"])
    @admin_required
    def admin_seller_status(seller_id):
        status = request.form.get("status", "")
        if status not in ("Approved", "Suspended", "Pending"):
            flash("Unknown status.", "error")
        else:
            db.execute("UPDATE seller SET status = %s WHERE seller_id = %s", (status, seller_id))
            flash("Seller marked as %s." % status, "success")
        return redirect(url_for("admin_sellers"))

    @app.route("/admin/seller-items")
    @admin_required
    def admin_seller_items():
        seller_id = to_int(request.args.get("seller"))
        link = request.args.get("link", "")
        sql = "SELECT * FROM vw_seller_listings WHERE 1 = 1"
        params = []
        if seller_id:
            sql += " AND seller_id = %s"
            params.append(seller_id)
        if link == "new":
            sql += " AND product_id IS NULL"
        elif link == "store":
            sql += " AND product_id IS NOT NULL"
        sql += " ORDER BY listing_id DESC"
        items = db.query_all(sql, tuple(params))
        sellers = db.query_all("SELECT seller_id, business_name FROM seller ORDER BY business_name")
        return render_template("admin_seller_items.html", items=items, sellers=sellers,
                               seller_id=seller_id, link=link)

    @app.route("/admin/seller-items/<int:listing_id>/add-to-store", methods=["POST"])
    @admin_required
    def admin_add_to_store(listing_id):
        price = to_decimal(request.form.get("retail_price"))
        if price is None:
            flash("Please enter the selling price.", "error")
        else:
            try:
                result = db.call_procedure("sp_add_listing_to_store", (listing_id, price, 0))
                flash("Added to the store (product %s) with 0 stock. Order stock from the seller on the "
                      "Restock page." % result[2], "success")
            except db.DBError as e:
                flash(friendly(e), "error")
        return redirect(url_for("admin_seller_items"))

    # -------------------------------------------------------------- restock
    @app.route("/admin/restock/<int:product_id>")
    @admin_required
    def admin_restock_page(product_id):
        product = db.query_one("SELECT * FROM vw_product_supply WHERE product_id = %s", (product_id,))
        if not product:
            abort(404)
        available = db.query_all(
            "SELECT * FROM vw_seller_listings WHERE product_id = %s AND is_active = 1 "
            "AND seller_status = 'Approved' AND quantity_available >= min_order_qty ORDER BY unit_price",
            (product_id,))
        unavailable = db.query_all(
            "SELECT * FROM vw_seller_listings WHERE product_id = %s AND NOT (is_active = 1 "
            "AND seller_status = 'Approved' AND quantity_available >= min_order_qty)", (product_id,))
        active = db.query_one(
            "SELECT * FROM vw_purchase_orders WHERE product_id = %s AND status IN " + IN_PROGRESS +
            " ORDER BY po_id DESC LIMIT 1", (product_id,))
        history = db.query_all(
            "SELECT * FROM vw_purchase_orders WHERE product_id = %s ORDER BY po_id DESC LIMIT 5", (product_id,))
        return render_template("admin_restock.html", p=product, available=available,
                               unavailable=unavailable, active=active, history=history)

    @app.route("/admin/restock/order", methods=["POST"])
    @admin_required
    def admin_restock_order():
        listing_id = to_int(request.form.get("listing_id"))
        qty = to_int(request.form.get("quantity"))
        product_id = request.form.get("product_id")
        if listing_id is None or qty is None:
            flash("Please enter a valid quantity.", "error")
            return redirect(url_for("admin_restock_page", product_id=product_id))
        try:
            result = db.call_procedure("sp_order_from_listing",
                                       (listing_id, qty, request.form.get("note", ""), 0))
            flash("Order #%s sent to the seller. Stock increases when you receive it." % result[3], "success")
            return redirect(url_for("admin_po_detail", po_id=result[3]))
        except db.DBError as e:
            flash(friendly(e), "error")
            return redirect(url_for("admin_restock_page", product_id=product_id))

    @app.route("/admin/restock/request", methods=["POST"])
    @admin_required
    def admin_restock_request():
        product_id = to_int(request.form.get("product_id"))
        qty = to_int(request.form.get("quantity"))
        max_price = to_decimal(request.form.get("max_price")) if request.form.get("max_price") else None
        if product_id is None or qty is None:
            flash("Please enter a valid quantity.", "error")
            return redirect(url_for("admin_inventory"))
        try:
            result = db.call_procedure("sp_raise_restock_request",
                                       (product_id, qty, max_price, request.form.get("note", ""), 0))
            flash("Request #%s is now visible to all sellers." % result[4], "success")
            return redirect(url_for("admin_po_detail", po_id=result[4]))
        except db.DBError as e:
            flash(friendly(e), "error")
            return redirect(url_for("admin_restock_page", product_id=product_id))

    # ------------------------------------------------------ purchase orders
    @app.route("/admin/purchase-orders")
    @admin_required
    def admin_purchase_orders():
        status = request.args.get("status", "")
        if status in PO_STATUSES:
            rows = db.query_all("SELECT * FROM vw_purchase_orders WHERE status = %s ORDER BY po_id DESC", (status,))
        else:
            status = ""
            rows = db.query_all("SELECT * FROM vw_purchase_orders ORDER BY po_id DESC")
        return render_template("admin_purchase_orders.html", orders=rows, statuses=PO_STATUSES, selected=status)

    @app.route("/admin/purchase-orders/<int:po_id>")
    @admin_required
    def admin_po_detail(po_id):
        order = db.query_one("SELECT * FROM vw_purchase_orders WHERE po_id = %s", (po_id,))
        if not order:
            abort(404)
        history = db.query_all("SELECT old_status, new_status, changed_at FROM purchase_order_log "
                               "WHERE po_id = %s ORDER BY log_id", (po_id,))
        return render_template("admin_po_detail.html", o=order, history=history)

    @app.route("/admin/purchase-orders/<int:po_id>/action", methods=["POST"])
    @admin_required
    def admin_po_action(po_id):
        action = request.form.get("action", "")
        if action not in ("accept_offer", "reject_offer", "cancel", "receive"):
            flash("Unknown action.", "error")
        else:
            try:
                db.call_procedure("sp_admin_po_action", (po_id, action))
                flash({"accept_offer": "Offer accepted. The seller can now ship the goods.",
                       "reject_offer": "Offer rejected. The request is open to all sellers again.",
                       "cancel": "Order cancelled.",
                       "receive": "Goods received. The store inventory has been increased."}[action], "success")
            except db.DBError as e:
                flash(friendly(e), "error")
        return redirect(url_for("admin_po_detail", po_id=po_id))
