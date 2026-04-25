from datetime import datetime, date
from decimal import Decimal
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, current_app, send_from_directory
from flask_login import login_user, logout_user, login_required, current_user
from .extensions import db
from .models import User, Project, Invoice, Payment, RailwayUsageSnapshot
from .services.mercadopago import create_payment_preference, fetch_payment
from .sync import clear_unpaid_project_invoices, sync_project_from_railway, sync_all_projects
from werkzeug.utils import secure_filename
import os

bp = Blueprint("main", __name__)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def money(value):
    return Decimal(str(value or "0").replace(",", "."))


def save_project_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    if not filename:
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in {"png", "jpg", "jpeg", "webp", "gif"}:
        flash("Use uma imagem PNG, JPG, WEBP ou GIF.", "warning")
        return None
    upload_dir = current_app.config.get("PROJECT_UPLOAD_FOLDER") or os.path.join("/data", "uploads", "projects")
    os.makedirs(upload_dir, exist_ok=True)
    unique_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{filename}"
    file_storage.save(os.path.join(upload_dir, unique_name))
    return f"/uploads/projects/{unique_name}"


@bp.route("/uploads/projects/<path:filename>")
def uploaded_project_image(filename):
    upload_dir = current_app.config.get("PROJECT_UPLOAD_FOLDER") or os.path.join("/data", "uploads", "projects")
    return send_from_directory(upload_dir, filename, max_age=86400)


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.admin_dashboard" if current_user.is_admin else "main.client_dashboard"))
    recent_projects = Project.query.order_by(Project.created_at.desc()).limit(6).all()
    return render_template("landing.html", recent_projects=recent_projects)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if User.query.filter_by(email=request.form["email"].lower()).first():
            flash("Este e-mail já está cadastrado.", "danger")
            return redirect(url_for("main.register"))
        if not request.form.get("accepted_terms"):
            flash("Você precisa aceitar os termos.", "danger")
            return redirect(url_for("main.register"))
        user = User(
            name=request.form["name"],
            email=request.form["email"].lower(),
            company=request.form.get("company"),
            whatsapp=request.form.get("whatsapp"),
            document=request.form.get("document"),
            accepted_terms=True,
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Conta criada com sucesso.", "success")
        return redirect(url_for("main.client_dashboard"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form["email"].lower()).first()
        if not user or not user.check_password(request.form["password"]):
            flash("E-mail ou senha inválidos.", "danger")
            return redirect(url_for("main.login"))
        if not user.is_admin and not user.is_active_client:
            flash("Seu acesso está bloqueado. Fale com o suporte.", "danger")
            return redirect(url_for("main.login"))
        login_user(user)
        return redirect(url_for("main.admin_dashboard" if user.is_admin else "main.client_dashboard"))
    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("main.index"))


@bp.route("/dashboard")
@login_required
def client_dashboard():
    projects = Project.query.filter_by(client_id=current_user.id).all()
    invoices = Invoice.query.filter_by(client_id=current_user.id).order_by(Invoice.created_at.desc()).limit(5).all()
    total_current = sum([p.current_cost or 0 for p in projects])
    total_estimated = sum([p.estimated_cost or 0 for p in projects])
    next_invoice = Invoice.query.filter_by(client_id=current_user.id, status="pendente").order_by(Invoice.due_date.asc()).first()
    return render_template("client/dashboard.html", projects=projects, invoices=invoices, total_current=total_current, total_estimated=total_estimated, next_invoice=next_invoice)


@bp.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    if not current_user.is_admin and project.client_id != current_user.id:
        abort(403)
    invoices = Invoice.query.filter_by(project_id=project.id).order_by(Invoice.created_at.desc()).all()
    snapshots = RailwayUsageSnapshot.query.filter_by(project_id=project.id).order_by(RailwayUsageSnapshot.created_at.desc()).limit(8).all()
    return render_template("client/project_detail.html", project=project, invoices=invoices, snapshots=snapshots)


@bp.route("/invoices")
@login_required
def invoices():
    query = Invoice.query
    if not current_user.is_admin:
        query = query.filter_by(client_id=current_user.id)
    invoices = query.order_by(Invoice.created_at.desc()).all()
    return render_template("client/invoices.html", invoices=invoices)


@bp.route("/invoices/<int:invoice_id>")
@login_required
def invoice_detail(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if not current_user.is_admin and invoice.client_id != current_user.id:
        abort(403)
    return render_template("client/invoice_detail.html", invoice=invoice)


@bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
@login_required
def pay_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if not current_user.is_admin and invoice.client_id != current_user.id:
        abort(403)
    preference = create_payment_preference(invoice)
    invoice.payment_link = preference["payment_link"]
    invoice.mp_preference_id = preference["preference_id"]
    invoice.mp_external_reference = preference["external_reference"]
    invoice.status = "aguardando pagamento"
    db.session.commit()
    if preference.get("demo"):
        flash("Modo demo: configure MERCADO_PAGO_ACCESS_TOKEN para gerar checkout real.", "warning")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    return redirect(invoice.payment_link)


@bp.route("/payments")
@login_required
def payments():
    query = Payment.query.join(Invoice)
    if not current_user.is_admin:
        query = query.filter(Invoice.client_id == current_user.id)
    payments = query.order_by(Payment.paid_at.desc()).all()
    return render_template("client/payments.html", payments=payments)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.name = request.form["name"]
        current_user.company = request.form.get("company")
        current_user.whatsapp = request.form.get("whatsapp")
        current_user.document = request.form.get("document")
        if request.form.get("password"):
            current_user.set_password(request.form["password"])
        db.session.commit()
        flash("Perfil atualizado.", "success")
        return redirect(url_for("main.profile"))
    return render_template("client/profile.html")


@bp.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    clients_count = User.query.filter_by(role="client").count()
    projects_count = Project.query.count()
    pending_invoices = Invoice.query.filter(Invoice.status.in_(["pendente", "aguardando pagamento", "atrasado"])).count()
    paid_total = db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0)).scalar()
    projects = Project.query.order_by(Project.created_at.desc()).limit(6).all()
    invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(6).all()
    return render_template("admin/dashboard.html", clients_count=clients_count, projects_count=projects_count, pending_invoices=pending_invoices, paid_total=paid_total, projects=projects, invoices=invoices)


@bp.route("/admin/clients")
@login_required
@admin_required
def admin_clients():
    clients = User.query.filter_by(role="client").order_by(User.created_at.desc()).all()
    return render_template("admin/clients.html", clients=clients)


@bp.route("/admin/clients/<int:user_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_client(user_id):
    client = User.query.get_or_404(user_id)
    client.is_active_client = not client.is_active_client
    db.session.commit()
    flash("Status do cliente atualizado.", "success")
    return redirect(url_for("main.admin_clients"))


@bp.route("/admin/projects/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_project_new():
    clients = User.query.filter_by(role="client", is_active_client=True).all()
    if request.method == "POST":
        project = Project(
            client_id=int(request.form["client_id"]),
            name=request.form["name"],
            railway_project_id=(request.form.get("railway_project_id") or "").strip(),
            status=request.form.get("status", "ativo"),
            plan=request.form.get("plan"),
            image_url=(request.form.get("image_url") or "").strip() or save_project_image(request.files.get("image_file")),
            description=request.form.get("description"),
            tech_stack=request.form.get("tech_stack"),
            monthly_value=Decimal("0.00"),
            usage_limit=Decimal("0.00"),
            current_cost=Decimal("0.00"),
            estimated_cost=Decimal("0.00"),
            management_fee=Decimal("0.00"),
            notes=request.form.get("notes"),
        )
        db.session.add(project)
        db.session.commit()
        if project.railway_project_id:
            try:
                sync_project_from_railway(project)
                flash("Projeto criado e sincronizado com a Railway.", "success")
            except Exception as exc:
                project.sync_status = "erro"
                project.sync_error = str(exc)
                project.last_sync_at = datetime.utcnow()
                db.session.commit()
                flash(f"Projeto criado, mas a sincronização Railway falhou: {exc}", "warning")
        else:
            flash("Projeto criado.", "success")
        return redirect(url_for("main.project_detail", project_id=project.id))
    return render_template("admin/project_form.html", clients=clients, project=None)


@bp.route("/admin/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def admin_project_edit(project_id):
    project = Project.query.get_or_404(project_id)
    clients = User.query.filter_by(role="client").all()
    if request.method == "POST":
        old_railway_project_id = project.railway_project_id
        new_railway_project_id = (request.form.get("railway_project_id") or "").strip()

        project.client_id = int(request.form["client_id"])
        project.name = request.form["name"]
        project.railway_project_id = new_railway_project_id
        project.status = request.form.get("status")
        project.plan = request.form.get("plan")
        uploaded_image = save_project_image(request.files.get("image_file"))
        project.image_url = uploaded_image or (request.form.get("image_url") or "").strip() or project.image_url
        project.description = request.form.get("description")
        project.tech_stack = request.form.get("tech_stack")
        project.notes = request.form.get("notes")

        if old_railway_project_id != new_railway_project_id:
            clear_unpaid_project_invoices(project)
            project.railway_internal_name = None
            project.railway_environment_id = None
            project.railway_service_id = None
            project.public_url = None
            project.current_cost = Decimal("0.00")
            project.estimated_cost = Decimal("0.00")
            project.monthly_value = Decimal("0.00")
            project.management_fee = Decimal("0.00")
            project.last_cost_update = datetime.utcnow()

        db.session.commit()

        if project.railway_project_id:
            try:
                sync_project_from_railway(project)
                flash("Projeto atualizado e sincronizado com a Railway.", "success")
            except Exception as exc:
                project.sync_status = "erro"
                project.sync_error = str(exc)
                project.last_sync_at = datetime.utcnow()
                db.session.commit()
                flash(f"Projeto atualizado, mas a sincronização Railway falhou: {exc}", "warning")
        else:
            flash("Projeto atualizado.", "success")
        return redirect(url_for("main.project_detail", project_id=project.id))
    return render_template("admin/project_form.html", clients=clients, project=project)



@bp.route("/admin/projects/<int:project_id>/sync-railway", methods=["POST"])
@login_required
@admin_required
def admin_project_sync_railway(project_id):
    project = Project.query.get_or_404(project_id)
    try:
        sync_project_from_railway(project)
        if project.sync_status == "parcial":
            flash("Projeto sincronizado parcialmente. Confira o aviso nos detalhes.", "warning")
        else:
            flash("Projeto sincronizado com a Railway.", "success")
    except Exception as exc:
        project.sync_status = "erro"
        project.sync_error = str(exc)
        project.last_sync_at = datetime.utcnow()
        db.session.commit()
        flash(f"Erro ao sincronizar Railway: {exc}", "danger")
    return redirect(url_for("main.project_detail", project_id=project.id))


@bp.route("/admin/sync-railway", methods=["POST"])
@login_required
@admin_required
def admin_sync_all_railway():
    total, ok, failed = sync_all_projects()
    if failed:
        flash(f"Sincronização finalizada: {ok}/{total} projetos atualizados, {failed} com erro.", "warning")
    else:
        flash(f"Sincronização finalizada: {ok}/{total} projetos atualizados.", "success")
    return redirect(url_for("main.admin_dashboard"))

@bp.route("/admin/invoices/new", methods=["GET", "POST"])
@login_required
@admin_required
def admin_invoice_new():
    projects = Project.query.order_by(Project.name.asc()).all()
    if request.method == "POST":
        project = Project.query.get_or_404(int(request.form["project_id"]))
        railway_cost = money(request.form.get("railway_cost"))
        management_fee = money(request.form.get("management_fee"))
        discounts = money(request.form.get("discounts"))
        fines = money(request.form.get("fines"))
        total = railway_cost + management_fee + fines - discounts
        invoice = Invoice(
            number=request.form["number"],
            client_id=project.client_id,
            project_id=project.id,
            period=request.form["period"],
            railway_cost=railway_cost,
            management_fee=management_fee,
            discounts=discounts,
            fines=fines,
            total=total,
            status=request.form.get("status", "pendente"),
            due_date=datetime.strptime(request.form["due_date"], "%Y-%m-%d").date(),
        )
        db.session.add(invoice)
        db.session.commit()
        flash("Fatura criada.", "success")
        return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))
    suggested_number = f"{date.today().year}-{Invoice.query.count()+1:04d}"
    return render_template("admin/invoice_form.html", projects=projects, suggested_number=suggested_number)


@bp.route("/admin/invoices/<int:invoice_id>/mark-paid", methods=["POST"])
@login_required
@admin_required
def admin_mark_paid(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    invoice.status = "pago"
    payment = Payment(
        invoice_id=invoice.id,
        amount=invoice.total,
        status="pago",
        method=request.form.get("method", "manual"),
        transaction_code=request.form.get("transaction_code", f"manual-{invoice.id}"),
    )
    db.session.add(payment)
    db.session.commit()
    flash("Fatura marcada como paga.", "success")
    return redirect(url_for("main.invoice_detail", invoice_id=invoice.id))


@bp.route("/webhooks/mercadopago", methods=["POST", "GET"])
def mercadopago_webhook():
    payload = request.get_json(silent=True) or request.args.to_dict()
    payment_id = payload.get("data", {}).get("id") if isinstance(payload.get("data"), dict) else payload.get("id")
    topic = payload.get("type") or payload.get("topic")
    if not payment_id or topic not in ["payment", "payment.created", "payment.updated"]:
        return jsonify({"ok": True})
    payment_data = fetch_payment(payment_id)
    if not payment_data:
        return jsonify({"ok": True, "demo": True})
    external_reference = payment_data.get("external_reference")
    invoice = Invoice.query.filter_by(mp_external_reference=external_reference).first()
    if invoice and payment_data.get("status") == "approved":
        invoice.status = "pago"
        exists = Payment.query.filter_by(transaction_code=str(payment_id)).first()
        if not exists:
            db.session.add(Payment(
                invoice_id=invoice.id,
                amount=Decimal(str(payment_data.get("transaction_amount", invoice.total))),
                status="pago",
                method=payment_data.get("payment_method_id"),
                transaction_code=str(payment_id),
                receipt_url=payment_data.get("transaction_details", {}).get("external_resource_url"),
            ))
        db.session.commit()
    return jsonify({"ok": True})
