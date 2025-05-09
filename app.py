# app.py - Main application file using SQLite for reliability
# This serves as both a standalone app and a fallback for other versions

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import json
import os
import qrcode
from io import BytesIO
import base64
import csv
import secrets
import string
from functools import wraps

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your_secret_key_here')

# Configure SQLite database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///queue_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# Models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    companies = db.relationship('Company', backref='admin', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    service_type = db.Column(db.String(100), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('admin.id'), nullable=False)
    company_code = db.Column(db.String(20), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cashiers = db.relationship('Cashier', backref='company', lazy=True, cascade="all, delete-orphan")

class Cashier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cashier_number = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    customers = db.relationship('Customer', backref='cashier', lazy=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cashier_id = db.Column(db.Integer, db.ForeignKey('cashier.id'), nullable=False)
    otp = db.Column(db.String(6), nullable=False)
    join_time = db.Column(db.DateTime, default=datetime.utcnow)
    served_time = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='waiting')  # waiting, serving, served, delayed, removed
    delays = db.Column(db.Integer, default=0)
    position = db.Column(db.Integer, nullable=False)
    serving_start_time = db.Column(db.DateTime)

class QueueHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    cashier_number = db.Column(db.Integer, nullable=False)
    otp = db.Column(db.String(6), nullable=False)
    join_time = db.Column(db.DateTime, nullable=False)
    served_time = db.Column(db.DateTime)
    wait_time_seconds = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False)
    delays = db.Column(db.Integer, default=0)

# Create database tables
with app.app_context():
    db.create_all()
    print("Database tables created")

# Helper Functions
def generate_company_code():
    letters = string.ascii_uppercase
    return ''.join(secrets.choice(letters) for _ in range(6))

def generate_otp():
    digits = string.digits
    return ''.join(secrets.choice(digits) for _ in range(6))

def calculate_wait_time(cashier_id):
    customers = Customer.query.filter_by(cashier_id=cashier_id, status='waiting').order_by(Customer.position).all()
    history = QueueHistory.query.filter_by(cashier_number=Cashier.query.get(cashier_id).cashier_number)
    
    # Calculate average serving time (default to 3 minutes if not enough data)
    served_customers = history.filter(QueueHistory.wait_time_seconds.isnot(None)).all()
    if len(served_customers) > 5:
        avg_serving_time = sum(c.wait_time_seconds for c in served_customers[-5:]) / 5  # Last 5 customers
    else:
        avg_serving_time = 180  # 3 minutes default
    
    return avg_serving_time

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in first.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def index():
    if 'admin_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "database": "sqlite"}), 200

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))
        
        if Admin.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        
        admin = Admin(username=username)
        admin.set_password(password)
        
        db.session.add(admin)
        db.session.commit()
        
        flash('Account created successfully. Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and admin.check_password(password):
            session['admin_id'] = admin.id
            flash('Logged in successfully.', 'success')
            return redirect(url_for('dashboard'))
        
        flash('Invalid username or password.', 'danger')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('admin_id', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    admin_id = session.get('admin_id')
    companies = Company.query.filter_by(admin_id=admin_id).all()
    return render_template('dashboard.html', companies=companies)

@app.route('/create_company', methods=['GET', 'POST'])
@login_required
def create_company():
    if request.method == 'POST':
        name = request.form.get('name')
        service_type = request.form.get('service_type')
        num_cashiers = int(request.form.get('num_cashiers'))
        
        # Generate unique company code
        while True:
            company_code = generate_company_code()
            if not Company.query.filter_by(company_code=company_code).first():
                break
        
        # Create company
        company = Company(
            name=name,
            service_type=service_type,
            admin_id=session.get('admin_id'),
            company_code=company_code
        )
        
        db.session.add(company)
        db.session.flush()  # Get company ID without committing
        
        # Create cashiers
        for i in range(1, num_cashiers + 1):
            cashier = Cashier(
                company_id=company.id,
                cashier_number=i,
                is_active=True
            )
            db.session.add(cashier)
        
        db.session.commit()
        
        flash('Company created successfully.', 'success')
        return redirect(url_for('manage_company', company_id=company.id))
    
    return render_template('create_company.html')

@app.route('/manage_company/<int:company_id>')
@login_required
def manage_company(company_id):
    company = Company.query.get_or_404(company_id)
    
    # Check if admin owns this company
    if company.admin_id != int(session.get('admin_id')):
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('dashboard'))
    
    cashiers = Cashier.query.filter_by(company_id=company_id).order_by(Cashier.cashier_number).all()
    
    # Get queue stats
    stats = {
        'total_served': QueueHistory.query.filter_by(company_id=company_id, status='served').count(),
        'total_delayed': QueueHistory.query.filter_by(company_id=company_id).filter(QueueHistory.delays > 0).count(),
        'avg_wait_time': 0
    }
    
    served_customers = QueueHistory.query.filter_by(company_id=company_id, status='served').all()
    if served_customers:
        total_wait_time = sum(c.wait_time_seconds or 0 for c in served_customers)
        stats['avg_wait_time'] = total_wait_time / len(served_customers) if len(served_customers) > 0 else 0
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(f"{request.host_url}join/{company.company_code}")
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered)
    qr_code = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    return render_template('manage_company.html', company=company, cashiers=cashiers, stats=stats, qr_code=qr_code)

@app.route('/api/get_cashier_queue/<int:cashier_id>')
@login_required
def get_cashier_queue(cashier_id):
    cashier = Cashier.query.get_or_404(cashier_id)
    
    # Check if admin owns this company
    company = Company.query.get(cashier.company_id)
    if company.admin_id != int(session.get('admin_id')):
        return jsonify({'error': 'Unauthorized access'}), 403
    
    customers = Customer.query.filter_by(cashier_id=cashier_id).order_by(Customer.position).all()
    
    queue_data = []
    for customer in customers:
        # Calculate estimated wait time
        position = customer.position
        estimated_wait_time = int(position * calculate_wait_time(cashier_id))
        
        queue_data.append({
            'id': customer.id,
            'otp': customer.otp,
            'position': position,
            'status': customer.status,
            'delays': customer.delays,
            'join_time': customer.join_time.strftime('%H:%M:%S'),
            'estimated_wait_time': estimated_wait_time,
            'serving_start_time': customer.serving_start_time.strftime('%H:%M:%S') if customer.serving_start_time else None
        })
    
    return jsonify({
        'cashier_number': cashier.cashier_number,
        'is_active': cashier.is_active,
        'queue': queue_data
    })

@app.route('/api/toggle_cashier/<int:cashier_id>', methods=['POST'])
@login_required
def toggle_cashier(cashier_id):
    cashier = Cashier.query.get_or_404(cashier_id)
    
    # Check if admin owns this company
    company = Company.query.get(cashier.company_id)
    if company.admin_id != int(session.get('admin_id')):
        return jsonify({'error': 'Unauthorized access'}), 403
    
    # Toggle cashier active status
    cashier.is_active = not cashier.is_active
    db.session.commit()
    
    # Emit socket event to notify all clients
    socketio.emit('cashier_status_change', {
        'cashier_id': cashier_id,
        'is_active': cashier.is_active,
        'company_code': company.company_code
    })
    
    return jsonify({'success': True, 'is_active': cashier.is_active})

@app.route('/queue_status/<otp>')
def queue_status(otp):
    customer = Customer.query.filter_by(otp=otp).first_or_404()
    cashier = Cashier.query.get(customer.cashier_id)
    company = Company.query.get(cashier.company_id)
    
    # Calculate estimated wait time
    estimated_wait_seconds = customer.position * calculate_wait_time(cashier.id)
    
    response = app.make_response(render_template(
        'queue_status.html',
        customer=customer,
        cashier=cashier,
        company=company,
        estimated_wait_seconds=estimated_wait_seconds
    ))
    
    # Set cache headers 
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/api/check_status/<otp>')
def check_status(otp):
    customer = Customer.query.filter_by(otp=otp).first_or_404()
    cashier = Cashier.query.get(customer.cashier_id)
    
    # Calculate estimated wait time
    estimated_wait_seconds = customer.position * calculate_wait_time(cashier.id)
    
    # Calculate time since serving started (if applicable)
    serving_time_passed = None
    if customer.serving_start_time:
        serving_time_passed = (datetime.utcnow() - customer.serving_start_time).total_seconds()
    
    response = jsonify({
        'position': customer.position,
        'status': customer.status,
        'cashier_number': cashier.cashier_number,
        'estimated_wait_seconds': estimated_wait_seconds,
        'serving_time_passed': serving_time_passed,
        'delays': customer.delays
    })
    
    # Set cache headers
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@app.route('/join/<company_code>')
def join_queue_page(company_code):
    company = Company.query.filter_by(company_code=company_code).first_or_404()
    cashiers = Cashier.query.filter_by(company_id=company.id, is_active=True).all()
    
    return render_template('join_queue.html', company=company, cashiers=cashiers)

@app.route('/api/join_queue/<company_code>', methods=['POST'])
def join_queue(company_code):
    company = Company.query.filter_by(company_code=company_code).first_or_404()
    
    # Find the cashier with the shortest queue
    cashiers = Cashier.query.filter_by(company_id=company.id, is_active=True).all()
    
    if not cashiers:
        return jsonify({'error': 'No active cashiers available'}), 400
    
    shortest_queue_cashier = None
    min_queue_length = float('inf')
    
    for cashier in cashiers:
        queue_length = Customer.query.filter_by(
            cashier_id=cashier.id, 
            status='waiting'
        ).count()
        
        if queue_length < min_queue_length:
            min_queue_length = queue_length
            shortest_queue_cashier = cashier
    
    # Generate OTP
    while True:
        otp = generate_otp()
        if not Customer.query.filter_by(otp=otp).first():
            break
    
    # Calculate position
    position = min_queue_length + 1
    
    # Create customer in queue
    customer = Customer(
        cashier_id=shortest_queue_cashier.id,
        otp=otp,
        position=position
    )
    
    db.session.add(customer)
    db.session.commit()
    
    # Calculate estimated wait time
    estimated_wait_seconds = position * calculate_wait_time(shortest_queue_cashier.id)
    
    # If this is the first customer for this cashier, mark as serving
    if position == 1:
        customer.status = 'serving'
        customer.serving_start_time = datetime.utcnow()
        db.session.commit()
        
        # Emit socket event to notify the customer
        socketio.emit('customer_turn', {
            'otp': customer.otp,
            'cashier_number': shortest_queue_cashier.cashier_number,
            'company_code': company.company_code
        })
    
    return jsonify({
        'success': True,
        'otp': otp,
        'position': position,
        'cashier_number': shortest_queue_cashier.cashier_number,
        'estimated_wait_seconds': estimated_wait_seconds
    })

# For WSGI servers like gunicorn
application = app

if __name__ == '__main__':
    # Get port from environment variable
    port = int(os.getenv('PORT', 5000))
    print(f"Starting server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)