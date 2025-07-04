#!/usr/bin/env python3
"""
Main Dashboard Application with Authentication and Stripe Integration

This Flask application provides:
- User authentication (login/signup)
- Dashboard with plans management
- Stripe subscription integration
- User profile and billing history
"""

import os
import sys
import json
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import stripe
from get_user_plan_history import UserPlanHistoryFetcher
# Add logging imports
import logging
from logging_config import StripeIntegrationLoggerSetup, log_system_info, log_startup_banner

# Load environment variables
load_dotenv()

# Setup logging
log_setup = StripeIntegrationLoggerSetup(log_level=logging.DEBUG)
loggers = log_setup.setup_all_loggers()
logger = loggers['main']
db_logger = loggers['database']

log_startup_banner(logger)
log_system_info(logger)

# Configure Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
BASE_URL = os.getenv('BASE_URL', 'http://localhost:4242')

# Database configuration
DATABASE_URL = os.getenv('PSQL_DB_URL')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def hash_password(password):
    """Hash password with salt"""
    salt = secrets.token_hex(32)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return salt + pwdhash.hex()

def verify_password(stored_password, provided_password):
    """Verify password against stored hash"""
    salt = stored_password[:64]
    stored_hash = stored_password[64:]
    pwdhash = hashlib.pbkdf2_hmac('sha256', provided_password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return pwdhash.hex() == stored_hash

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    """Get current logged-in user"""
    if 'user_id' not in session:
        return None
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM app_users WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()
        return dict(user) if user else None
    finally:
        cur.close()
        conn.close()

def get_stripe_customer_for_user(user_email):
    """Get Stripe customer information for user"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM customers WHERE email = %s", (user_email,))
        customer = cur.fetchone()
        return dict(customer) if customer else None
    finally:
        cur.close()
        conn.close()

# Routes
@app.route('/')
def index():
    """Home page"""
    user = get_current_user()
    return render_template('index.html', user=user)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM app_users WHERE email = %s", (email,))
            user = cur.fetchone()
            
            if user and verify_password(user['password_hash'], password):
                session['user_id'] = user['id']
                session['user_email'] = user['email']
                flash('Login successful!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid email or password', 'error')
        finally:
            cur.close()
            conn.close()
    
    return render_template('auth/login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Signup page"""
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return render_template('auth/signup.html')
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Check if user already exists
            cur.execute("SELECT id FROM app_users WHERE email = %s", (email,))
            if cur.fetchone():
                flash('Email already registered', 'error')
                return render_template('auth/signup.html')
            
            # Create new user
            password_hash = hash_password(password)
            cur.execute("""
                INSERT INTO app_users (name, email, password_hash, created_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (name, email, password_hash, datetime.now()))
            
            user_id = cur.fetchone()['id']
            conn.commit()
            
            session['user_id'] = user_id
            session['user_email'] = email
            flash('Account created successfully!', 'success')
            return redirect(url_for('dashboard'))
            
        except Exception as e:
            conn.rollback()
            flash(f'Error creating account: {str(e)}', 'error')
        finally:
            cur.close()
            conn.close()
    
    return render_template('auth/signup.html')

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard"""
    user = get_current_user()
    
    # Get Stripe customer info and subscription history
    stripe_customer = get_stripe_customer_for_user(user['email'])
    plan_history = None
    current_subscription = None
    
    if stripe_customer:
        try:
            # Get current subscription with latest data
            current_subscription = load_latest_subscription_for_user(user['email'])
            logger.info(f"Current subscription for {user['email']}: {current_subscription.get('product_name', 'None')}")
            
            # Get comprehensive plan history
            fetcher = UserPlanHistoryFetcher()
            plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
            plan_history = plan_data if 'error' not in plan_data else None
            fetcher.close()
        except Exception as e:
            logger.error(f"Error fetching dashboard data for {user['email']}: {e}")
    
    return render_template('dashboard/dashboard.html', 
                         user=user, 
                         stripe_customer=stripe_customer,
                         plan_history=plan_history,
                         current_subscription=current_subscription)

@app.route('/plans')
def plans():
    """Plans page - show available plans"""
    user = get_current_user()
    
    # Get available plans from database
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.*, pr.name as product_name, pr.description as product_description
            FROM prices p
            JOIN products pr ON p.product_id = pr.id
            WHERE p.active = true AND pr.active = true
            ORDER BY p.unit_amount
        """)
        available_plans = [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()
    
    # Get user's current subscription if logged in
    current_subscription = None
    stripe_customer = None
    
    if user:
        stripe_customer = get_stripe_customer_for_user(user['email'])
        if stripe_customer:
            try:
                fetcher = UserPlanHistoryFetcher()
                plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
                if 'error' not in plan_data:
                    current_plans = plan_data.get('summary', {}).get('current_plans', [])
                    current_subscription = current_plans[0] if current_plans else None
                fetcher.close()
            except Exception as e:
                print(f"Error fetching current subscription: {e}")
    
    return render_template('plans/plans.html', 
                         user=user,
                         available_plans=available_plans,
                         current_subscription=current_subscription,
                         stripe_customer=stripe_customer)

@app.route('/about')
def about():
    """About page"""
    user = get_current_user()
    return render_template('about.html', user=user)

@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    # Get detailed plan history and current subscription
    plan_history = None
    current_subscription = None
    
    if stripe_customer:
        try:
            # Get current subscription with latest data
            current_subscription = load_latest_subscription_for_user(user['email'])
            logger.info(f"Profile - Current subscription for {user['email']}: {current_subscription.get('product_name', 'None')}")
            
            # Get comprehensive plan history
            fetcher = UserPlanHistoryFetcher()
            plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
            plan_history = plan_data if 'error' not in plan_data else None
            fetcher.close()
        except Exception as e:
            logger.error(f"Error fetching profile data for {user['email']}: {e}")
    
    return render_template('profile/profile.html', 
                         user=user,
                         stripe_customer=stripe_customer,
                         plan_history=plan_history,
                         current_subscription=current_subscription)

@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    """Update user profile"""
    user = get_current_user()
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    
    if not name or not email:
        flash('Name and email are required', 'error')
        return redirect(url_for('profile'))
    
    if '@' not in email:
        flash('Please enter a valid email address', 'error')
        return redirect(url_for('profile'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Check if email is already taken by another user
        if email != user['email']:
            cur.execute("SELECT id FROM app_users WHERE email = %s AND id != %s", (email, user['id']))
            if cur.fetchone():
                flash('Email already taken by another user', 'error')
                return redirect(url_for('profile'))
        
        # Update user information
        cur.execute("""
            UPDATE app_users 
            SET name = %s, email = %s, updated_at = %s
            WHERE id = %s
        """, (name, email, datetime.now(), user['id']))
        
        conn.commit()
        
        # Update session if email changed
        if email != user['email']:
            session['user_email'] = email
        
        flash('Profile updated successfully!', 'success')
        
    except Exception as e:
        conn.rollback()
        flash(f'Error updating profile: {str(e)}', 'error')
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('profile'))

@app.route('/api/plan-history/<customer_id>')
@login_required
def api_plan_history(customer_id):
    """API endpoint to get plan history"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    # Security check - ensure user can only access their own data
    if not stripe_customer or stripe_customer['stripe_id'] != customer_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        fetcher = UserPlanHistoryFetcher()
        plan_data = fetcher.get_comprehensive_plan_history(customer_id)
        fetcher.close()
        
        # Convert datetime objects for JSON serialization
        def json_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        return jsonify(plan_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/invoice/<invoice_id>/pdf')
@login_required
def get_invoice_pdf(invoice_id):
    """Get invoice PDF URL from Stripe"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    if not stripe_customer:
        return jsonify({'error': 'No Stripe customer found'}), 404
    
    try:
        # Get the invoice from Stripe
        invoice = stripe.Invoice.retrieve(invoice_id)
        
        # Security check - ensure this invoice belongs to the current user
        if invoice.customer != stripe_customer['stripe_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Get the PDF URL
        if invoice.invoice_pdf:
            return jsonify({'pdf_url': invoice.invoice_pdf})
        else:
            return jsonify({'error': 'PDF not available for this invoice'}), 404
            
    except stripe.error.StripeError as e:
        return jsonify({'error': f'Stripe error: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Stripe integration routes copied from server.py
@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    """Create Stripe checkout session"""
    user = get_current_user()
    lookup_key = request.form.get('lookup_key')
    
    if not lookup_key:
        return jsonify({'error': 'Missing lookup_key'}), 400
    
    try:
        prices = stripe.Price.list(lookup_keys=[lookup_key], expand=['data.product'])
        
        # Create or get customer
        stripe_customer = get_stripe_customer_for_user(user['email'])
        if stripe_customer:
            customer_id = stripe_customer['stripe_id']
        else:
            # Create new Stripe customer
            customer = stripe.Customer.create(
                email=user['email'],
                name=user['name']
            )
            customer_id = customer.id
            
            # Store in database
            conn = get_db_connection()
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT INTO customers (stripe_id, email, name)
                    VALUES (%s, %s, %s)
                """, (customer_id, user['email'], user['name']))
                conn.commit()
            finally:
                cur.close()
                conn.close()
        
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            billing_address_collection='auto',
            line_items=[{
                'price': prices.data[0].id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=BASE_URL + '/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=BASE_URL + '/cancel',
        )
        
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/create-portal-session', methods=['POST'])
@login_required
def create_portal_session():
    """Create Stripe customer portal session"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    if not stripe_customer:
        return jsonify({'error': 'No Stripe customer found'}), 404
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer['stripe_id'],
            return_url=BASE_URL + '/dashboard',
        )
        return redirect(portal_session.url, code=303)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/create-billing-portal-session', methods=['POST'])
@login_required
def create_billing_portal_session():
    """Create a Stripe billing portal session"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    if not stripe_customer:
        flash('No billing information found', 'error')
        return redirect(url_for('profile'))
    
    try:
        # Create billing portal session
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer['stripe_id'],
            return_url=f"{BASE_URL}/profile"
        )
        
        # Redirect directly to the billing portal
        return redirect(portal_session.url, code=303)
    except Exception as e:
        logger.error(f"Error creating billing portal session: {e}")
        flash('Unable to create billing portal session', 'error')
        return redirect(url_for('profile'))

@app.route('/analytics')
@login_required
def analytics():
    """Advanced analytics page"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    # Get detailed plan history and analytics
    analytics_data = None
    if stripe_customer:
        try:
            fetcher = UserPlanHistoryFetcher()
            plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
            
            if 'error' not in plan_data:
                # Generate additional analytics
                analytics_data = generate_analytics_data(plan_data)
            
            fetcher.close()
        except Exception as e:
            print(f"Error fetching analytics data: {e}")
    
    return render_template('dashboard/analytics.html', 
                         user=user,
                         stripe_customer=stripe_customer,
                         analytics_data=analytics_data)

def generate_analytics_data(plan_data):
    """Generate analytics data from plan history"""
    analytics = {
        'monthly_spend': [],
        'plan_changes_timeline': [],
        'usage_metrics': {},
        'cost_trends': {},
        'recommendations': []
    }
    
    # Calculate monthly spending
    from datetime import datetime, timedelta
    from collections import defaultdict
    
    monthly_spend = defaultdict(float)
    
    # Process prorations for monthly spending
    for proration in plan_data.get('summary', {}).get('prorations', []):
        if proration.get('date'):
            month_key = proration['date'].strftime('%Y-%m')
            amount = proration.get('amount', 0)
            if isinstance(amount, (int, float)):
                monthly_spend[month_key] += amount / 100  # Convert from cents
    
    # Convert to list format for charts
    analytics['monthly_spend'] = [
        {'month': month, 'amount': amount} 
        for month, amount in sorted(monthly_spend.items())
    ]
    
    # Plan changes timeline
    for change in plan_data.get('summary', {}).get('plan_changes', []):
        analytics['plan_changes_timeline'].append({
            'date': change.get('timestamp'),
            'from_plan': change.get('from_plan', {}).get('product_name', 'None'),
            'to_plan': change.get('to_plan', {}).get('product_name', 'Unknown'),
            'reason': 'upgrade' if change.get('to_plan', {}).get('unit_amount', 0) > change.get('from_plan', {}).get('unit_amount', 0) else 'downgrade'
        })
    
    # Usage metrics
    analytics['usage_metrics'] = {
        'total_subscription_days': (datetime.now() - plan_data.get('summary', {}).get('customer_created', datetime.now())).days,
        'plan_changes_count': len(plan_data.get('summary', {}).get('plan_changes', [])),
        'average_monthly_cost': sum(monthly_spend.values()) / max(len(monthly_spend), 1),
        'total_lifetime_value': sum(monthly_spend.values())
    }
    
    # Simple recommendations
    if analytics['usage_metrics']['plan_changes_count'] > 3:
        analytics['recommendations'].append({
            'type': 'stability',
            'message': 'You\'ve changed plans frequently. Consider reviewing your usage patterns.',
            'icon': 'fa-chart-line'
        })
    
    if analytics['usage_metrics']['average_monthly_cost'] > 100:
        analytics['recommendations'].append({
            'type': 'cost',
            'message': 'Consider reviewing your plan to optimize costs.',
            'icon': 'fa-piggy-bank'
        })
    
    return analytics

# Add missing API routes from server.py
@app.route('/products', methods=['GET'])
def get_products():
    try:
        prices = stripe.Price.list(active=True, expand=["data.product"])
        products = []
        for price in prices.data:
            product = price.product
            products.append({
                "id": product.get("id"),
                "name": product.get("name", ""),
                "price_id": price.id,
                "lookup_key": price.lookup_key,
                "unit_amount": price.unit_amount,
                "currency": price.currency,
                "interval": price.recurring["interval"] if price.recurring else None,
                "description": product.get("description", "")
            })
        return jsonify(products)
    except Exception as e:
        logger.error(f"Error fetching products: {e}")
        return jsonify({"error": "Failed to fetch products"}), 500

@app.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    user_id = get_current_user_id()
    sub = load_latest_subscription_for_user(user_id)
    if not sub or 'stripe_id' not in sub:
        return jsonify({'error': 'No active subscription found'}), 400
    try:
        stripe.Subscription.delete(sub['stripe_id'])
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE subscriptions SET status = 'canceled', canceled_at = CURRENT_TIMESTAMP WHERE stripe_id = %s", (sub['stripe_id'],))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('checkout_cancel'))
    except Exception as e:
        logger.error(f"Cancel subscription error: {e}")
        return jsonify({'error': 'Failed to cancel'}), 500

@app.route('/update-subscription', methods=['POST'])
@login_required
def update_subscription():
    user_id = get_current_user_id()
    lookup_key = request.form.get('lookup_key')
    sub = load_latest_subscription_for_user(user_id)
    if not sub or 'id' not in sub:
        return jsonify({'error': 'No active subscription'}), 400
    try:
        prices = stripe.Price.list(lookup_keys=[lookup_key], expand=['data.product'])
        new_price_id = prices.data[0].id
        subscription = stripe.Subscription.retrieve(sub['stripe_id'])
        item_id = subscription['items']['data'][0]['id']
        stripe.Subscription.modify(
            sub['stripe_id'],
            cancel_at_period_end=False,
            proration_behavior='create_prorations',
            items=[{'id': item_id, 'price': new_price_id}]
        )
        return redirect(url_for('checkout_success'))
    except Exception as e:
        logger.error(f"Update subscription error: {e}")
        return jsonify({'error': 'Update failed'}), 500

@app.route('/my-subscription', methods=['GET'])
@login_required
def my_subscription():
    user_email = get_current_user_id()  # This returns email
    sub = load_latest_subscription_for_user(user_email)
    
    if sub and sub.get('status') == 'active':
        # Get the actual product name from the subscription data
        product_name = sub.get('product_name', 'Unknown Plan')
        price_nickname = sub.get('price_nickname', '')
        
        # Use nickname if available, otherwise use product name
        display_name = price_nickname if price_nickname else product_name
        
        logger.info(f"API - Current subscription for {user_email}: {display_name}")
        
        return jsonify({
            "has_active": True,
            "plan": sub.get('lookup_key', ''),
            "price_id": sub.get('unit_amount', 0),
            "stripe_subscription_id": sub.get('stripe_id', ''),
            "status": sub.get('status', ''),
            "product_name": product_name,
            "display_name": display_name,
            "currency": sub.get('currency', 'usd')
        })
    else:
        logger.info(f"API - No active subscription found for {user_email}")
        return jsonify({"has_active": False})

# Add all the database upsert functions from server.py
def upsert_customer(data, conn, cur):
    """Upsert customer data from Stripe webhook with comprehensive logging"""
    customer_id = data.get('id', 'unknown')
    customer_email = data.get('email', 'no-email')
    
    try:
        db_logger.info(f"Starting customer upsert for Stripe ID: {customer_id}, email: {customer_email}")
        
        address = data.get('address', {}) or {}
        db_logger.debug(f"Customer {customer_id} address data: {address}")
        
        cur.execute("""
            INSERT INTO customers (
                stripe_id, email, name, phone, address_line1, address_line2,
                address_city, address_state, address_postal_code, address_country,
                currency, balance, tax_exempt, delinquent, invoice_prefix,
                preferred_locales, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                email = EXCLUDED.email,
                name = EXCLUDED.name,
                phone = EXCLUDED.phone,
                address_line1 = EXCLUDED.address_line1,
                address_line2 = EXCLUDED.address_line2,
                address_city = EXCLUDED.address_city,
                address_state = EXCLUDED.address_state,
                address_postal_code = EXCLUDED.address_postal_code,
                address_country = EXCLUDED.address_country,
                currency = EXCLUDED.currency,
                balance = EXCLUDED.balance,
                tax_exempt = EXCLUDED.tax_exempt,
                delinquent = EXCLUDED.delinquent,
                invoice_prefix = EXCLUDED.invoice_prefix,
                preferred_locales = EXCLUDED.preferred_locales,
                metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id;
        """, (
            customer_id,
            data.get('email', ''),
            data.get('name', ''),
            data.get('phone', ''),
            address.get('line1', ''),
            address.get('line2', ''),
            address.get('city', ''),
            address.get('state', ''),
            address.get('postal_code', ''),
            address.get('country', ''),
            data.get('currency', ''),
            data.get('balance', 0),
            data.get('tax_exempt', 'none'),
            data.get('delinquent', False),
            data.get('invoice_prefix', ''),
            json.dumps(data.get('preferred_locales', [])),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_id = result['id']
        
        db_logger.info(f"Successfully upserted customer {customer_id} with database ID: {db_id}")
        logger.debug(f"Customer upsert completed: Stripe ID {customer_id} -> DB ID {db_id}")
        
        return db_id
        
    except psycopg2.Error as e:
        db_logger.error(f"PostgreSQL error upserting customer {customer_id}: {e}")
        logger.error(f"Database error in customer upsert for {customer_id}: {e}")
        raise
    except Exception as e:
        db_logger.error(f"Unexpected error upserting customer {customer_id}: {e}")
        logger.error(f"Unexpected error in customer upsert for {customer_id}: {e}")
        raise

def upsert_product(data, conn, cur):
    """Upsert product from Stripe event"""
    stripe_id = data.get('id')
    
    try:
        cur.execute("""
            INSERT INTO products (
                stripe_id, name, description, active, metadata
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                name = EXCLUDED.name,
                description = EXCLUDED.description,
                active = EXCLUDED.active,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            stripe_id,
            data.get('name', ''),
            data.get('description', ''),
            data.get('active', True),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted product {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting product {stripe_id}: {e}")
        raise

def upsert_price(data, conn, cur):
    """Upsert price from Stripe event"""
    stripe_id = data.get('id')
    product_stripe_id = data.get('product')
    
    # Get product database ID
    product_id = None
    if product_stripe_id:
        cur.execute("SELECT id FROM products WHERE stripe_id = %s", (product_stripe_id,))
        row = cur.fetchone()
        if row:
            product_id = row['id']
    
    try:
        recurring = data.get('recurring', {}) or {}
        
        cur.execute("""
            INSERT INTO prices (
                stripe_id, product_id, currency, unit_amount, billing_scheme,
                recurring_interval, recurring_interval_count, lookup_key, nickname,
                active, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                product_id = EXCLUDED.product_id,
                currency = EXCLUDED.currency,
                unit_amount = EXCLUDED.unit_amount,
                billing_scheme = EXCLUDED.billing_scheme,
                recurring_interval = EXCLUDED.recurring_interval,
                recurring_interval_count = EXCLUDED.recurring_interval_count,
                lookup_key = EXCLUDED.lookup_key,
                nickname = EXCLUDED.nickname,
                active = EXCLUDED.active,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            stripe_id,
            product_id,
            data.get('currency', '').upper(),
            data.get('unit_amount'),
            data.get('billing_scheme', 'per_unit'),
            recurring.get('interval'),
            recurring.get('interval_count', 1),
            data.get('lookup_key'),
            data.get('nickname'),
            data.get('active', True),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted price {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting price {stripe_id}: {e}")
        raise

def upsert_subscription_new(data, conn, cur):
    """Upsert subscription using new schema"""
    stripe_id = data.get('id')
    customer_stripe_id = data.get('customer')
    
    # Get customer database ID
    customer_id = None
    if customer_stripe_id:
        cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
        row = cur.fetchone()
        if row:
            customer_id = row['id']
    
    if not customer_id:
        db_logger.error(f"Cannot upsert subscription: customer not found for {customer_stripe_id}")
        return None
    
    try:
        cur.execute("""
            INSERT INTO subscriptions (
                stripe_id, customer_id, status, current_period_start, current_period_end,
                created_at_stripe, started_at, ended_at, canceled_at, cancel_at_period_end,
                collection_method, currency, trial_start, trial_end, metadata
            ) VALUES (
                %s, %s, %s, TO_TIMESTAMP(%s), TO_TIMESTAMP(%s),
                TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), %s,
                %s, %s, TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), %s
            )
            ON CONFLICT (stripe_id) DO UPDATE SET
                status = EXCLUDED.status,
                current_period_start = EXCLUDED.current_period_start,
                current_period_end = EXCLUDED.current_period_end,
                started_at = EXCLUDED.started_at,
                ended_at = EXCLUDED.ended_at,
                canceled_at = EXCLUDED.canceled_at,
                cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                collection_method = EXCLUDED.collection_method,
                currency = EXCLUDED.currency,
                trial_start = EXCLUDED.trial_start,
                trial_end = EXCLUDED.trial_end,
                metadata = EXCLUDED.metadata,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id;
        """, (
            stripe_id,
            customer_id,
            data.get('status'),
            data.get('current_period_start'),
            data.get('current_period_end'),
            data.get('created', 0) if data.get('created') else None,
            data.get('start_date'),
            data.get('ended_at'),
            data.get('canceled_at'),
            data.get('cancel_at_period_end', False),
            data.get('collection_method', 'charge_automatically'),
            data.get('currency'),
            data.get('trial_start'),
            data.get('trial_end'),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        subscription_db_id = result['id']
        
        # Handle subscription items
        items = data.get('items', {}).get('data', [])
        for item in items:
            price_stripe_id = item.get('price', {}).get('id')
            if price_stripe_id:
                # Get price database ID
                cur.execute("SELECT id FROM prices WHERE stripe_id = %s", (price_stripe_id,))
                price_row = cur.fetchone()
                if price_row:
                    price_db_id = price_row['id']
                    
                    # Upsert subscription item
                    cur.execute("""
                        INSERT INTO subscription_items (
                            stripe_id, subscription_id, price_id, quantity, metadata
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (stripe_id) DO UPDATE SET
                            price_id = EXCLUDED.price_id,
                            quantity = EXCLUDED.quantity,
                            metadata = EXCLUDED.metadata
                    """, (
                        item.get('id'),
                        subscription_db_id,
                        price_db_id,
                        item.get('quantity', 1),
                        json.dumps(item.get('metadata', {}))
                    ))
        
        db_logger.info(f"Upserted subscription {stripe_id} -> DB ID {subscription_db_id}")
        return subscription_db_id
        
    except Exception as e:
        db_logger.error(f"Error upserting subscription {stripe_id}: {e}")
        raise

def upsert_invoice_new(data, conn, cur):
    """Upsert invoice using new schema"""
    stripe_id = data.get('id')
    customer_stripe_id = data.get('customer')
    subscription_stripe_id = data.get('subscription')
    
    # Get foreign key IDs
    customer_id = None
    if customer_stripe_id:
        cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
        row = cur.fetchone()
        if row:
            customer_id = row['id']
    
    subscription_id = None
    if subscription_stripe_id:
        cur.execute("SELECT id FROM subscriptions WHERE stripe_id = %s", (subscription_stripe_id,))
        row = cur.fetchone()
        if row:
            subscription_id = row['id']
    
    try:
        cur.execute("""
            INSERT INTO invoices (
                stripe_id, customer_id, subscription_id, status, billing_reason,
                collection_method, currency, amount_due, amount_paid, amount_remaining,
                subtotal, total, tax_amount, created_at_stripe, due_date,
                finalized_at, paid_at, period_start, period_end, hosted_invoice_url,
                invoice_pdf, receipt_number, account_country, account_name,
                attempt_count, attempted, auto_advance, metadata
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, TO_TIMESTAMP(%s), TO_TIMESTAMP(%s),
                TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), TO_TIMESTAMP(%s), %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (stripe_id) DO UPDATE SET
                status = EXCLUDED.status,
                amount_due = EXCLUDED.amount_due,
                amount_paid = EXCLUDED.amount_paid,
                amount_remaining = EXCLUDED.amount_remaining,
                subtotal = EXCLUDED.subtotal,
                total = EXCLUDED.total,
                tax_amount = EXCLUDED.tax_amount,
                due_date = EXCLUDED.due_date,
                finalized_at = EXCLUDED.finalized_at,
                paid_at = EXCLUDED.paid_at,
                hosted_invoice_url = EXCLUDED.hosted_invoice_url,
                invoice_pdf = EXCLUDED.invoice_pdf,
                receipt_number = EXCLUDED.receipt_number,
                attempt_count = EXCLUDED.attempt_count,
                attempted = EXCLUDED.attempted,
                auto_advance = EXCLUDED.auto_advance,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            stripe_id, customer_id, subscription_id, data.get('status'), data.get('billing_reason'),
            data.get('collection_method'), data.get('currency', '').upper(),
            data.get('amount_due', 0), data.get('amount_paid', 0), data.get('amount_remaining', 0),
            data.get('subtotal', 0), data.get('total', 0), data.get('tax', 0),
            data.get('created', 0), data.get('due_date'),
            data.get('status_transitions', {}).get('finalized_at'),
            data.get('status_transitions', {}).get('paid_at'),
            data.get('period_start'), data.get('period_end'),
            data.get('hosted_invoice_url'), data.get('invoice_pdf'),
            data.get('receipt_number'), data.get('account_country'),
            data.get('account_name'), data.get('attempt_count', 0),
            data.get('attempted', False), data.get('auto_advance', False),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted invoice {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting invoice {stripe_id}: {e}")
        raise

def store_stripe_event(event, conn, cur):
    """Store raw Stripe event for audit purposes"""
    event_id = event.get('id', 'unknown')
    event_type = event.get('type', 'unknown')
    
    try:
        cur.execute("""
            INSERT INTO stripe_events (
                stripe_event_id, event_type, api_version, created_at, livemode,
                pending_webhooks, request_id, raw_data, processing_status
            ) VALUES (%s, %s, %s, TO_TIMESTAMP(%s), %s,
                %s, %s, %s, %s)
            """, (
                event_id,
                event_type,
                event.get('api_version', ''),
                event.get('created', 0),
                event.get('livemode', False),
                event.get('pending_webhooks', 0),
                event.get('request', {}).get('id', ''),
                json.dumps(event),
                'pending'
            ))
        
        conn.commit()
        logger.info(f"Stored Stripe event {event_id} of type {event_type}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error storing Stripe event {event_id}: {e}")

# Helper functions for Stripe integration
def load_latest_subscription_for_user(email):
    """Load latest subscription for user with proper SQL query and sync with Stripe"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # First get the customer
        cur.execute("SELECT stripe_id FROM customers WHERE email = %s", (email,))
        customer_row = cur.fetchone()
        
        if not customer_row:
            logger.warning(f"No customer found for email {email}")
            return {}
        
        customer_stripe_id = customer_row['stripe_id']
        
        # Get active subscription from Stripe to ensure we have the latest data
        try:
            stripe_subscriptions = stripe.Subscription.list(
                customer=customer_stripe_id,
                status='active',
                limit=1
            )
            
            if stripe_subscriptions.data:
                # Sync the subscription data with our database
                latest_stripe_sub = stripe_subscriptions.data[0]
                upsert_subscription_new(latest_stripe_sub, conn, cur)
                conn.commit()
                logger.info(f"Synced subscription data for customer {customer_stripe_id}")
        except Exception as stripe_error:
            logger.error(f"Error fetching subscription from Stripe: {stripe_error}")
        
        # Now query our database for the subscription
        cur.execute("""
            SELECT s.*, c.email, p.lookup_key, p.unit_amount, pr.name as product_name,
                   p.nickname as price_nickname
            FROM subscriptions s
            JOIN customers c ON s.customer_id = c.id
            LEFT JOIN subscription_items si ON s.id = si.subscription_id
            LEFT JOIN prices p ON si.price_id = p.id
            LEFT JOIN products pr ON p.product_id = pr.id
            WHERE c.email = %s AND s.status = 'active'
            ORDER BY s.updated_at DESC 
            LIMIT 1;
        """, (email,))
        row = cur.fetchone()
        if row:
            result = dict(row)
            logger.info(f"Retrieved subscription for {email}: {result.get('product_name', 'Unknown')}")
            return result
        else:
            logger.warning(f"No active subscription found for {email}")
            return {}
    except Exception as e:
        logger.error(f"Error loading subscription for user {email}: {e}")
        return {}
    finally:
        cur.close()
        conn.close()

def get_current_user_id():
    """Get current user ID for compatibility with server.py functions"""
    return session.get('user_email', 'unknown@example.com')

# Add a test route to display current user information
@app.route('/test-user')
@login_required
def test_user():
    """Test route to display current user information"""
    user = get_current_user()
    return jsonify({
        'user_id': user['id'],
        'email': user['email'],
        'name': user['name'],
        'session_data': dict(session)
    })

# Add a health check route
@app.route('/health')
def health():
    """Health check route"""
    return jsonify({'status': 'ok', 'message': 'Service is healthy'}), 200

# Add a route to trigger a test email
@app.route('/send-test-email')
@login_required
def send_test_email():
    """Send a test email to the logged-in user"""
    user = get_current_user()
    
    # Here you would integrate with your email sending service
    # For example, using Flask-Mail or any other service
    try:
        # Dummy implementation - replace with real email sending code
        logger.info(f"Sending test email to {user['email']}")
        # mail.send_email(subject="Test Email", recipients=[user['email']], body="This is a test email from your app.")
        
        flash('Test email sent successfully!', 'success')
    except Exception as e:
        logger.error(f"Error sending test email: {e}")
        flash('Error sending test email. Please try again later.', 'error')
    
    return redirect(url_for('profile'))

# Add a route to display logs
@app.route('/logs')
@login_required
def logs():
    """Display application logs"""
    # For security, restrict access to logs based on user role or other criteria
    user = get_current_user()
    if user['role'] != 'admin':
        flash('You do not have permission to view logs', 'error')
        return redirect(url_for('index'))
    
    # Read and display logs
    try:
        with open('app.log', 'r') as f:
            log_data = f.readlines()
        
        # Render logs in a preformatted text block
        return render_template('logs.html', logs=log_data)
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        flash('Error reading logs. Please try again later.', 'error')
        return redirect(url_for('index'))

# Add a route to clear logs
@app.route('/clear-logs', methods=['POST'])
@login_required
def clear_logs():
    """Clear application logs"""
    user = get_current_user()
    if user['role'] != 'admin':
        flash('You do not have permission to clear logs', 'error')
        return redirect(url_for('index'))
    
    # Clear logs by truncating the log file
    try:
        with open('app.log', 'w'):
            pass  # Truncate the file
        flash('Logs cleared successfully', 'success')
    except Exception as e:
        logger.error(f"Error clearing logs: {e}")
        flash('Error clearing logs. Please try again later.', 'error')
    
    return redirect(url_for('logs'))

# Add a route to view billing history
@app.route('/billing-history')
@login_required
def billing_history():
    """View billing history for the logged-in user"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    invoices = []
    if stripe_customer:
        try:
            # List all invoices for the customer
            invoices = stripe.Invoice.list(customer=stripe_customer['stripe_id'], limit=100)
        except Exception as e:
            logger.error(f"Error fetching invoices for customer {stripe_customer['stripe_id']}: {e}")
            flash('Error fetching invoices. Please try again later.', 'error')
    
    return render_template('billing/history.html', user=user, invoices=invoices)

# Add a route to view a specific invoice
@app.route('/invoice/<invoice_id>')
@login_required
def view_invoice(invoice_id):
    """View details of a specific invoice"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    invoice = None
    if stripe_customer:
        try:
            # Retrieve the invoice from Stripe
            invoice = stripe.Invoice.retrieve(invoice_id)
            
            # Security check - ensure this invoice belongs to the current user
            if invoice.customer != stripe_customer['stripe_id']:
                return jsonify({'error': 'Unauthorized'}), 403
        except Exception as e:
            logger.error(f"Error fetching invoice {invoice_id}: {e}")
            flash('Error fetching invoice. Please try again later.', 'error')
    
    return render_template('billing/invoice.html', user=user, invoice=invoice)

# Add a route to download invoice PDF
@app.route('/invoice/<invoice_id>/download')
@login_required
def download_invoice_pdf(invoice_id):
    """Download invoice PDF from Stripe"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    if not stripe_customer:
        return jsonify({'error': 'No Stripe customer found'}), 404
    
    try:
        # Get the invoice from Stripe
        invoice = stripe.Invoice.retrieve(invoice_id)
        
        # Security check - ensure this invoice belongs to the current user
        if invoice.customer != stripe_customer['stripe_id']:
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Get the PDF file
        pdf_url = invoice.invoice_pdf
        if not pdf_url:
            return jsonify({'error': 'PDF not available for this invoice'}), 404
        
        # Download the PDF file
        import requests
        response = requests.get(pdf_url)
        response.raise_for_status()  # Raise an error for bad responses
        
        # Send the PDF file as a response
        from flask import send_file
        from io import BytesIO
        return send_file(BytesIO(response.content), 
                         attachment_filename=f"invoice_{invoice_id}.pdf", 
                         as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add a route to update payment method
@app.route('/update-payment-method', methods=['POST'])
@login_required
def update_payment_method():
    """Update payment method for the logged-in user"""
    user = get_current_user()
    stripe_customer = get_stripe_customer_for_user(user['email'])
    
    if not stripe_customer:
        return jsonify({'error': 'No Stripe customer found'}), 404
    
    payment_method_id = request.form.get('payment_method_id')
    if not payment_method_id:
        return jsonify({'error': 'Missing payment_method_id'}), 400
    
    try:
        # Attach the new payment method to the customer
        stripe.PaymentMethod.attach(payment_method_id, customer=stripe_customer['stripe_id'])
        
        # Update the default payment method for the customer
        stripe.Customer.modify(
            stripe_customer['stripe_id'],
            invoice_settings={
                'default_payment_method': payment_method_id
            }
        )
        
        flash('Payment method updated successfully!', 'success')
    except Exception as e:
        logger.error(f"Error updating payment method for customer {stripe_customer['stripe_id']}: {e}")
        flash('Error updating payment method. Please try again later.', 'error')
    
    return redirect(url_for('profile'))

# Add a route to handle Stripe webhook events
@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    event = None
    
    # Verify the webhook signature
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        # Invalid payload
        logger.error(f"Invalid payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.error(f"Invalid signature: {e}")
        return jsonify({'error': 'Invalid signature'}), 401
    
    # Handle the event
    try:
        logger.info(f"Received event: {event['type']}")
        
        # Handle different event types
        if event['type'] == 'customer.created':
            customer_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_customer(customer_data, conn, cur)
        
        elif event['type'] == 'customer.updated':
            customer_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_customer(customer_data, conn, cur)
        
        elif event['type'] == 'customer.deleted':
            customer_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Soft delete the customer in the database
                    cur.execute("""
                        UPDATE customers 
                        SET deleted_at = %s 
                        WHERE stripe_id = %s
                    """, (datetime.now(), customer_data['id']))
        
        elif event['type'] == 'subscription.created':
            subscription_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_subscription_new(subscription_data, conn, cur)
        
        elif event['type'] == 'subscription.updated':
            subscription_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_subscription_new(subscription_data, conn, cur)
        
        elif event['type'] == 'subscription.deleted':
            subscription_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Soft delete the subscription in the database
                    cur.execute("""
                        UPDATE subscriptions 
                        SET canceled_at = %s, status = 'canceled' 
                        WHERE stripe_id = %s
                    """, (datetime.now(), subscription_data['id']))
        
        elif event['type'] == 'invoice.created':
            invoice_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_invoice_new(invoice_data, conn, cur)
        
        elif event['type'] == 'invoice.updated':
            invoice_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    upsert_invoice_new(invoice_data, conn, cur)
        
        elif event['type'] == 'invoice.payment_succeeded':
            invoice_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Update invoice status to paid
                    cur.execute("""
                        UPDATE invoices 
                        SET status = 'paid', paid_at = %s 
                        WHERE stripe_id = %s
                    """, (datetime.now(), invoice_data['id']))
        
        elif event['type'] == 'invoice.payment_failed':
            invoice_data = event['data']['object']
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Update invoice status to unpaid
                    cur.execute("""
                        UPDATE invoices 
                        SET status = 'unpaid' 
                        WHERE stripe_id = %s
                    """, (invoice_data['id'],))
        
        # Acknowledge the event
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Error handling event {event['type']}: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# Application startup
if __name__ == '__main__':
    # Ensure users table exists
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(256) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info("✅ App users table ready")
    except Exception as e:
        logger.warning(f"⚠️  Warning: Could not create app_users table: {e}")
    finally:
        cur.close()
        conn.close()
    
    logger.info("🚀 Starting Flask application on port 4242")
    app.run(debug=True, port=4242)