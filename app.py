# =====================================================================
#  GreenCart  -  E-Commerce Order Processing System  (Flask backend)
#  STEP 10 : new storefront design + animated landing page + admin panel
#
#  Local:   python app.py   ->   http://127.0.0.1:5000
#  Online:  deployed on Vercel (see the deployment steps)
# =====================================================================
import os
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, abort, send_from_directory)
import db
import auth
import accounts
import seller_portal
import restock
from helpers import (to_int, to_decimal, friendly, add_pct, safe_next,
                     customer_required, admin_required)

# Vercel serves files from the "public" folder, so Flask's own static folder is switched off
app = Flask(__name__, static_folder=None)

# Needed for login sessions. On Vercel set a SECRET_KEY environment variable.
app.secret_key = os.environ.get("SECRET_KEY", "greencart-secret-key-change-me")


# CSS and JavaScript files live in the "public" folder. Vercel serves them
# directly; these routes do the same job when you run the site on your own computer.
@app.route("/style.css")
def stylesheet():
    return send_from_directory(os.path.join(app.root_path, "public"), "style.css")


@app.route("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(app.root_path, "public", "assets"), filename)


@app.route("/uploads/<path:filename>")
def uploads(filename):
    # pictures sellers upload when running on your own computer (local mode)
    return send_from_directory(auth.UPLOAD_FOLDER, filename)


@app.errorhandler(404)
def page_not_found(error):
    return render_template("404.html"), 404

PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card", "Net Banking", "Cash on Delivery"]
ALL_STATUSES = ["Confirmed", "Packed", "Shipped", "Delivered", "Cancelled"]

# Which statuses the admin may choose next (the database trigger enforces
# the same rule, this just keeps the dropdown clean)
NEXT_STATUS = {
    "Confirmed": ["Packed", "Cancelled"],
    "Packed":    ["Shipped", "Cancelled"],
    "Shipped":   ["Delivered"],
    "Delivered": [],
    "Cancelled": [],
}


# Makes {{ cart_count }} and {{ nav_categories }} available in every template
# (the number on the cart icon and the category links in the store header)
@app.context_processor
def inject_store_data():
    data = {"cart_count": 0, "nav_categories": [], "auth_mode": auth.MODE}
    if "customer_id" in session:
        row = db.query_one(
            "SELECT COALESCE(SUM(ci.quantity), 0) AS n FROM cart c "
            "JOIN cart_item ci ON ci.cart_id = c.cart_id WHERE c.customer_id = %s",
            (session["customer_id"],))
        data["cart_count"] = row["n"]
        data["nav_categories"] = db.query_all(
            "SELECT category_id, category_name FROM category ORDER BY category_name")
    return data


# ---------------------------------------------------------------------
#  Home page
# ---------------------------------------------------------------------
@app.route("/")
def home():
    if "customer_id" in session:
        return redirect(url_for("shop"))
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    return render_template("landing.html")


# ---------------------------------------------------------------------
#  CUSTOMER: shop (collection page with search, filters and sorting)
#  Reads from the view vw_product_catalog
# ---------------------------------------------------------------------
# The sort options. Only these exact texts can reach the SQL (safe).
SORTS = {
    "featured": "product_id ASC",
    "best":     "units_sold DESC, product_name ASC",
    "rating":   "avg_rating DESC, review_count DESC, product_name ASC",
    "az":       "product_name ASC",
    "za":       "product_name DESC",
    "low":      "price ASC",
    "high":     "price DESC",
    "new":      "created_at DESC, product_id DESC",
}


@app.route("/shop")
@customer_required
def shop():
    search = request.args.get("search", "").strip()
    category_id = to_int(request.args.get("category"))
    in_stock = request.args.get("in_stock") == "1"
    min_price = to_decimal(request.args.get("min_price")) if request.args.get("min_price") else None
    max_price = to_decimal(request.args.get("max_price")) if request.args.get("max_price") else None
    sort = request.args.get("sort", "featured")
    if sort not in SORTS:
        sort = "featured"

    sql = "SELECT * FROM vw_product_catalog WHERE is_active = 1"
    params = []
    if search:
        sql += " AND (product_name LIKE %s OR description LIKE %s)"
        params += ["%" + search + "%", "%" + search + "%"]
    if category_id:
        sql += " AND category_id = %s"
        params.append(category_id)
    if in_stock:
        sql += " AND quantity_available > 0"
    if min_price is not None:
        sql += " AND price >= %s"
        params.append(min_price)
    if max_price is not None:
        sql += " AND price <= %s"
        params.append(max_price)
    sql += " ORDER BY " + SORTS[sort]
    products = db.query_all(sql, tuple(params))

    categories = db.query_all(
        "SELECT c.category_id, c.category_name, c.description, COUNT(p.product_id) AS product_count "
        "FROM category c LEFT JOIN product p ON p.category_id = c.category_id AND p.is_active = 1 "
        "GROUP BY c.category_id, c.category_name, c.description ORDER BY c.category_name")
    price_range = db.query_one("SELECT MIN(price) AS lo, MAX(price) AS hi FROM product WHERE is_active = 1")
    current = next((c for c in categories if c["category_id"] == category_id), None)

    return render_template("shop.html", products=products, categories=categories, current=current,
                           price_range=price_range, sort=sort, search=search, in_stock=in_stock,
                           category_id=category_id,
                           min_price=request.args.get("min_price", ""),
                           max_price=request.args.get("max_price", ""))


# ---------------------------------------------------------------------
#  CUSTOMER: product page (details, stock, reviews, add to cart)
# ---------------------------------------------------------------------
@app.route("/product/<int:product_id>")
@customer_required
def product_page(product_id):
    cid = session["customer_id"]
    product = db.query_one(
        "SELECT * FROM vw_product_catalog WHERE product_id = %s AND is_active = 1", (product_id,))
    if not product:
        abort(404)

    reviews = db.query_all(
        "SELECT * FROM vw_review_details WHERE product_id = %s ORDER BY review_date DESC", (product_id,))

    in_cart = db.query_one(
        "SELECT COALESCE(SUM(ci.quantity), 0) AS n FROM cart c "
        "JOIN cart_item ci ON ci.cart_id = c.cart_id "
        "WHERE c.customer_id = %s AND ci.product_id = %s", (cid, product_id))["n"]

    # Can this customer write a review? Only after the product was delivered.
    delivered = db.query_one(
        "SELECT COUNT(*) AS n FROM orders o JOIN order_item oi ON oi.order_id = o.order_id "
        "WHERE o.customer_id = %s AND oi.product_id = %s AND o.order_status = 'Delivered'",
        (cid, product_id))["n"] > 0
    reviewed = db.query_one(
        "SELECT COUNT(*) AS n FROM review WHERE customer_id = %s AND product_id = %s",
        (cid, product_id))["n"] > 0

    related = db.query_all(
        "SELECT * FROM vw_product_catalog WHERE category_id = %s AND product_id <> %s AND is_active = 1 "
        "ORDER BY units_sold DESC, product_name LIMIT 4", (product["category_id"], product_id))

    return render_template("product.html", p=product, reviews=reviews, in_cart=in_cart,
                           can_review=delivered and not reviewed, reviewed=reviewed, related=related)


# ---------------------------------------------------------------------
#  CUSTOMER: cart
# ---------------------------------------------------------------------
@app.route("/cart")
@customer_required
def cart():
    cid = session["customer_id"]
    items = db.query_all(
        "SELECT ci.cart_item_id, p.product_id, p.product_name, p.price, p.image_url, cat.category_name, "
        "       ci.quantity, (p.price * ci.quantity) AS line_total, i.quantity_available "
        "FROM cart c "
        "JOIN cart_item ci ON ci.cart_id = c.cart_id "
        "JOIN product p    ON p.product_id = ci.product_id "
        "JOIN category cat ON cat.category_id = p.category_id "
        "JOIN inventory i  ON i.product_id = p.product_id "
        "WHERE c.customer_id = %s ORDER BY ci.added_at", (cid,))
    total = db.query_one("SELECT fn_cart_total(%s) AS total", (cid,))["total"]   # our SQL function
    customer = db.query_one(
        "SELECT address, city, pincode FROM customer WHERE customer_id = %s", (cid,))
    default_address = "%s, %s %s" % (customer["address"], customer["city"], customer["pincode"])
    return render_template("cart.html", items=items, total=total,
                           default_address=default_address, methods=PAYMENT_METHODS)


@app.route("/cart/add", methods=["POST"])
@customer_required
def cart_add():
    try:
        quantity = int(request.form.get("quantity", 1))
        product_id = int(request.form["product_id"])
    except ValueError:
        flash("Invalid quantity.", "error")
        return redirect(safe_next("shop"))
    try:
        db.call_procedure("sp_add_to_cart", (session["customer_id"], product_id, quantity))
        flash("Added to your cart.", "success")
    except db.DBError as e:
        flash(db.error_message(e), "error")      # e.g. "more than the available stock"
    return redirect(safe_next("cart"))


@app.route("/cart/update", methods=["POST"])
@customer_required
def cart_update():
    try:
        quantity = int(request.form["quantity"])
        item_id = int(request.form["cart_item_id"])
    except ValueError:
        flash("Invalid quantity.", "error")
        return redirect(url_for("cart"))
    cid = session["customer_id"]
    try:
        if quantity < 1:
            db.execute("DELETE FROM cart_item WHERE cart_item_id = %s "
                       "AND cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)", (item_id, cid))
            flash("Item removed.", "success")
        else:
            db.execute("UPDATE cart_item SET quantity = %s WHERE cart_item_id = %s "
                       "AND cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)",
                       (quantity, item_id, cid))
            flash("Cart updated.", "success")
    except db.DBError as e:
        flash(db.error_message(e), "error")
    return redirect(url_for("cart"))


@app.route("/cart/remove", methods=["POST"])
@customer_required
def cart_remove():
    db.execute("DELETE FROM cart_item WHERE cart_item_id = %s "
               "AND cart_id = (SELECT cart_id FROM cart WHERE customer_id = %s)",
               (request.form["cart_item_id"], session["customer_id"]))
    flash("Item removed from your cart.", "success")
    return redirect(url_for("cart"))


# ---------------------------------------------------------------------
#  CUSTOMER: checkout  ->  calls the stored procedure sp_place_order
#  (one transaction: order + items + stock + payment, or nothing at all)
# ---------------------------------------------------------------------
@app.route("/checkout", methods=["POST"])
@customer_required
def checkout():
    method = request.form.get("payment_method", "")
    address = request.form.get("shipping_address", "").strip()
    if method not in PAYMENT_METHODS:
        flash("Please choose a payment method.", "error")
        return redirect(url_for("cart"))
    try:
        result = db.call_procedure("sp_place_order", (session["customer_id"], method, address, 0))
        order_id = result[3]                      # the OUT parameter
        flash("Order placed successfully! Your order number is #%s." % order_id, "success")
        return redirect(url_for("order_detail", order_id=order_id))
    except db.DBError as e:
        flash(db.error_message(e), "error")       # rolled back: nothing was saved
        return redirect(url_for("cart"))


# ---------------------------------------------------------------------
#  Order helpers (used by both customer and admin pages)
# ---------------------------------------------------------------------
def load_order(order_id, customer_id=None):
    sql = "SELECT * FROM vw_order_summary WHERE order_id = %s"
    params = [order_id]
    if customer_id is not None:                   # customers only see their own orders
        sql += " AND customer_id = %s"
        params.append(customer_id)
    order = db.query_one(sql, tuple(params))
    if not order:
        return None, [], []
    items = db.query_all("SELECT * FROM vw_order_details WHERE order_id = %s", (order_id,))
    history = db.query_all(
        "SELECT old_status, new_status, changed_at FROM order_status_log "
        "WHERE order_id = %s ORDER BY log_id", (order_id,))
    return order, items, history


# ---------------------------------------------------------------------
#  CUSTOMER: my orders, order details, cancel
# ---------------------------------------------------------------------
@app.route("/orders")
@customer_required
def orders():
    rows = db.query_all(
        "SELECT * FROM vw_order_summary WHERE customer_id = %s ORDER BY order_id DESC",
        (session["customer_id"],))
    return render_template("orders.html", orders=rows)


@app.route("/orders/<int:order_id>")
@customer_required
def order_detail(order_id):
    cid = session["customer_id"]
    order, items, history = load_order(order_id, cid)
    if not order:
        abort(404)
    reviewed = {r["product_id"] for r in db.query_all(
        "SELECT product_id FROM review WHERE customer_id = %s", (cid,))}
    return render_template("store_order.html", order=order, items=items, history=history,
                           reviewed=reviewed)


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
@customer_required
def order_cancel(order_id):
    try:
        db.call_procedure("sp_cancel_order", (order_id, session["customer_id"]))
        flash("Order cancelled. Stock was restored and any payment refunded.", "success")
    except db.DBError as e:
        flash(db.error_message(e), "error")
        mine = db.query_one("SELECT order_id FROM orders WHERE order_id = %s AND customer_id = %s",
                            (order_id, session["customer_id"]))
        if not mine:                              # not this customer's order
            return redirect(url_for("orders"))
    return redirect(url_for("order_detail", order_id=order_id))


# ---------------------------------------------------------------------
#  CUSTOMER: write a review (a trigger allows it only after delivery)
# ---------------------------------------------------------------------
@app.route("/review", methods=["POST"])
@customer_required
def review():
    product_id = request.form["product_id"]
    try:
        db.execute("INSERT INTO review (product_id, customer_id, rating, comment) VALUES (%s, %s, %s, %s)",
                   (product_id, session["customer_id"], request.form["rating"],
                    request.form.get("comment", "").strip()))
        flash("Thank you for your review!", "success")
    except db.DBError as e:
        if e.errno == 1062:
            flash("You have already reviewed this product.", "error")
        else:
            flash(db.error_message(e), "error")
    return redirect(url_for("product_page", product_id=product_id))


# ---------------------------------------------------------------------
#  ADMIN: login
# ---------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        admin = db.query_one(
            "SELECT admin_id FROM admin "
            "WHERE username = %s AND password_hash = SHA2(%s, 256)",
            (request.form["username"].strip(), request.form["password"]))
        if admin:
            session.clear()
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Wrong admin username or password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    stats = {
        "orders": db.query_one("SELECT COUNT(*) AS n FROM orders")["n"],
        "revenue": db.query_one(
            "SELECT COALESCE(SUM(total_amount), 0) AS n FROM orders "
            "WHERE order_status <> 'Cancelled'")["n"],
        "customers": db.query_one("SELECT COUNT(*) AS n FROM customer")["n"],
        "low_stock": db.query_one("SELECT COUNT(*) AS n FROM vw_low_stock")["n"],
        "pending_sellers": db.query_one("SELECT COUNT(*) AS n FROM seller WHERE status = 'Pending'")["n"],
        "offers": db.query_one("SELECT COUNT(*) AS n FROM purchase_order WHERE status = 'Offered'")["n"],
        "to_receive": db.query_one("SELECT COUNT(*) AS n FROM purchase_order WHERE status = 'Shipped'")["n"],
        "to_process": db.query_one(
            "SELECT COUNT(*) AS n FROM orders WHERE order_status IN ('Confirmed', 'Packed')")["n"],
    }
    recent = db.query_all("SELECT * FROM vw_order_summary ORDER BY order_id DESC LIMIT 5")
    return render_template("admin_dashboard.html", stats=stats, recent=recent)


# ---------------------------------------------------------------------
#  ADMIN: all orders + change order status
# ---------------------------------------------------------------------
@app.route("/admin/orders")
@admin_required
def admin_orders():
    status = request.args.get("status", "").strip()
    if status in ALL_STATUSES:
        rows = db.query_all(
            "SELECT * FROM vw_order_summary WHERE order_status = %s ORDER BY order_id DESC", (status,))
    else:
        status = ""
        rows = db.query_all("SELECT * FROM vw_order_summary ORDER BY order_id DESC")
    return render_template("admin_orders.html", orders=rows, statuses=ALL_STATUSES,
                           selected=status, next_status=NEXT_STATUS)


@app.route("/admin/orders/<int:order_id>")
@admin_required
def admin_order_detail(order_id):
    order, items, history = load_order(order_id)
    if not order:
        abort(404)
    return render_template("order_detail.html", order=order, items=items, history=history,
                           reviewed=set(), admin=True,
                           next_options=NEXT_STATUS.get(order["order_status"], []))


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def admin_update_status(order_id):
    new_status = request.form.get("new_status", "")
    if new_status not in ALL_STATUSES:
        flash("Please choose a valid status.", "error")
    else:
        try:
            # The procedure updates the order; triggers then log the change,
            # fill in shipment dates, mark Cash-on-Delivery as paid, or
            # restore stock and refund when the order is cancelled.
            db.call_procedure("sp_update_order_status", (order_id, new_status))
            flash("Order #%s is now %s." % (order_id, new_status), "success")
        except db.DBError as e:
            flash(db.error_message(e), "error")
    return redirect(safe_next("admin_orders"))


# =====================================================================
#  ADMIN: PRODUCTS
# =====================================================================
@app.route("/admin/products")
@admin_required
def admin_products():
    products = db.query_all(
        "SELECT p.product_id, p.product_name, c.category_name, p.price, p.is_active, "
        "       i.quantity_available, i.reorder_level, fn_stock_status(p.product_id) AS stock_status "
        "FROM product p "
        "JOIN category c  ON c.category_id = p.category_id "
        "JOIN inventory i ON i.product_id  = p.product_id "
        "ORDER BY p.product_id")
    return render_template("admin_products.html", products=products)


@app.route("/admin/products/new", methods=["GET", "POST"])
@admin_required
def admin_product_new():
    categories = db.query_all("SELECT category_id, category_name FROM category ORDER BY category_name")
    if request.method == "POST":
        f = request.form
        price = to_decimal(f.get("price"))
        stock = to_int(f.get("stock"))
        reorder = to_int(f.get("reorder_level"))
        image_url = f.get("image_url", "").strip()
        if price is None or stock is None or reorder is None:
            flash("Price, stock and reorder level must be numbers.", "error")
            return render_template("admin_product_form.html", product=f, categories=categories, is_new=True)
        if image_url and not image_url.startswith(("http://", "https://", "/")):
            flash("The image link must start with http://, https:// or /", "error")
            return render_template("admin_product_form.html", product=f, categories=categories, is_new=True)
        try:
            # one transaction: product + stock row (see 08_store_upgrade.sql)
            result = db.call_procedure("sp_add_product", (
                f["category_id"], f["product_name"].strip(), f.get("description", "").strip(),
                price, stock, reorder, image_url, 0))
            flash("Product added (id %s)." % result[7], "success")
            return redirect(url_for("admin_products"))
        except db.DBError as e:
            flash(friendly(e), "error")
            return render_template("admin_product_form.html", product=f, categories=categories, is_new=True)
    return render_template("admin_product_form.html",
                           product={"reorder_level": 5, "stock": 0}, categories=categories, is_new=True)


@app.route("/admin/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_product_edit(product_id):
    product = db.query_one("SELECT * FROM product WHERE product_id = %s", (product_id,))
    if not product:
        abort(404)
    categories = db.query_all("SELECT category_id, category_name FROM category ORDER BY category_name")
    if request.method == "POST":
        f = request.form
        price = to_decimal(f.get("price"))
        image_url = f.get("image_url", "").strip()
        if price is None:
            flash("Price must be a number.", "error")
        elif image_url and not image_url.startswith(("http://", "https://", "/")):
            flash("The image link must start with http://, https:// or /", "error")
        else:
            try:
                # If the price changes, a trigger writes the old and new price to price_history
                db.execute(
                    "UPDATE product SET category_id = %s, product_name = %s, description = %s, "
                    "price = %s, image_url = %s, is_active = %s WHERE product_id = %s",
                    (f["category_id"], f["product_name"].strip(), f.get("description", "").strip(),
                     price, image_url or None, 1 if f.get("is_active") else 0, product_id))
                flash("Product updated.", "success")
                return redirect(url_for("admin_products"))
            except db.DBError as e:
                flash(friendly(e), "error")
        product = dict(product)
        product.update(f.to_dict())
        product["is_active"] = 1 if f.get("is_active") else 0
    return render_template("admin_product_form.html", product=product, categories=categories, is_new=False)


@app.route("/admin/products/<int:product_id>/toggle", methods=["POST"])
@admin_required
def admin_product_toggle(product_id):
    db.execute("UPDATE product SET is_active = NOT is_active WHERE product_id = %s", (product_id,))
    flash("Product visibility changed.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/<int:product_id>/delete", methods=["POST"])
@admin_required
def admin_product_delete(product_id):
    try:
        db.execute("DELETE FROM product WHERE product_id = %s", (product_id,))
        flash("Product deleted.", "success")
    except db.DBError as e:
        if e.errno == 1451:        # foreign key: the product appears in past orders
            flash("This product has been ordered before, so it cannot be deleted. "
                  "Use Hide instead.", "error")
        else:
            flash(friendly(e), "error")
    return redirect(url_for("admin_products"))


# =====================================================================
#  ADMIN: CATEGORIES
# =====================================================================
@app.route("/admin/categories")
@admin_required
def admin_categories():
    categories = db.query_all(
        "SELECT c.category_id, c.category_name, c.description, COUNT(p.product_id) AS product_count "
        "FROM category c LEFT JOIN product p ON p.category_id = c.category_id "
        "GROUP BY c.category_id, c.category_name, c.description ORDER BY c.category_name")
    return render_template("admin_categories.html", categories=categories)


@app.route("/admin/categories/add", methods=["POST"])
@admin_required
def admin_category_add():
    name = request.form.get("category_name", "").strip()
    if not name:
        flash("Category name is required.", "error")
    else:
        try:
            db.execute("INSERT INTO category (category_name, description) VALUES (%s, %s)",
                       (name, request.form.get("description", "").strip()))
            flash("Category added.", "success")
        except db.DBError as e:
            flash("A category with this name already exists." if e.errno == 1062 else friendly(e), "error")
    return redirect(url_for("admin_categories"))


@app.route("/admin/categories/<int:category_id>/edit", methods=["POST"])
@admin_required
def admin_category_edit(category_id):
    name = request.form.get("category_name", "").strip()
    if not name:
        flash("Category name is required.", "error")
    else:
        try:
            db.execute("UPDATE category SET category_name = %s, description = %s WHERE category_id = %s",
                       (name, request.form.get("description", "").strip(), category_id))
            flash("Category updated.", "success")
        except db.DBError as e:
            flash("A category with this name already exists." if e.errno == 1062 else friendly(e), "error")
    return redirect(url_for("admin_categories"))


@app.route("/admin/categories/<int:category_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(category_id):
    try:
        db.execute("DELETE FROM category WHERE category_id = %s", (category_id,))
        flash("Category deleted.", "success")
    except db.DBError as e:
        if e.errno == 1451:        # foreign key (ON DELETE RESTRICT): products still use it
            flash("This category still has products. Move or delete them first.", "error")
        else:
            flash(friendly(e), "error")
    return redirect(url_for("admin_categories"))


# =====================================================================
#  ADMIN: INVENTORY
#  The admin cannot add stock by hand any more. Stock goes up only when a
#  seller's purchase order is received (see restock.py).
# =====================================================================
@app.route("/admin/inventory")
@admin_required
def admin_inventory():
    stock = db.query_all(
        "SELECT * FROM vw_product_supply ORDER BY FIELD(stock_status, 'Out of Stock', 'Low Stock', 'In Stock'), "
        "quantity_available, product_name")
    alerts = db.query_all(
        "SELECT a.alert_time, p.product_name, a.quantity_left FROM low_stock_alert a "
        "JOIN product p ON p.product_id = a.product_id ORDER BY a.alert_id DESC LIMIT 10")
    return render_template("admin_inventory.html", stock=stock, alerts=alerts)


# =====================================================================
#  ADMIN: CUSTOMERS
# =====================================================================
@app.route("/admin/customers")
@admin_required
def admin_customers():
    customers = db.query_all(
        "SELECT t.customer_id, t.full_name, t.email, t.city, t.orders_count, t.total_spent, t.tier, "
        "       c.phone, c.created_at "
        "FROM vw_top_customers t JOIN customer c ON c.customer_id = t.customer_id "
        "ORDER BY t.total_spent DESC, t.full_name")
    return render_template("admin_customers.html", customers=customers)


# =====================================================================
#  ADMIN: REPORTS (all data comes from the views and log tables)
# =====================================================================
@app.route("/admin/reports")
@admin_required
def admin_reports():
    by_category = add_pct(db.query_all(
        "SELECT * FROM vw_sales_by_category ORDER BY revenue DESC"), "revenue")
    top_products = add_pct(db.query_all(
        "SELECT * FROM vw_top_products ORDER BY units_sold DESC, revenue DESC LIMIT 5"), "units_sold")
    monthly = add_pct(db.query_all(
        "SELECT * FROM vw_monthly_sales ORDER BY sales_month"), "revenue")
    payments = add_pct(db.query_all(
        "SELECT pay.payment_method, COUNT(*) AS orders_count, SUM(pay.amount) AS amount "
        "FROM payment pay JOIN orders o ON o.order_id = pay.order_id "
        "WHERE o.order_status <> 'Cancelled' GROUP BY pay.payment_method ORDER BY amount DESC"), "amount")
    statuses = add_pct(db.query_all(
        "SELECT order_status, COUNT(*) AS n FROM orders GROUP BY order_status ORDER BY n DESC"), "n")
    top_customers = db.query_all(
        "SELECT * FROM vw_top_customers ORDER BY total_spent DESC LIMIT 5")
    price_log = db.query_all(
        "SELECT ph.changed_at, p.product_name, ph.old_price, ph.new_price FROM price_history ph "
        "JOIN product p ON p.product_id = ph.product_id ORDER BY ph.history_id DESC LIMIT 8")
    status_log = db.query_all(
        "SELECT changed_at, order_id, old_status, new_status FROM order_status_log "
        "ORDER BY log_id DESC LIMIT 8")
    return render_template("admin_reports.html", by_category=by_category, top_products=top_products,
                           monthly=monthly, payments=payments, statuses=statuses,
                           top_customers=top_customers, price_log=price_log, status_log=status_log)


# The other page files add their routes to this app
accounts.init(app)
seller_portal.init(app)
restock.init(app)


if __name__ == "__main__":
    app.run(debug=True)
