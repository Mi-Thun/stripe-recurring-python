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

# Load environment variables
load_dotenv()

# Configure Flask app
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
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
    
    if stripe_customer:
        try:
            fetcher = UserPlanHistoryFetcher()
            plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
            plan_history = plan_data if 'error' not in plan_data else None
            fetcher.close()
        except Exception as e:
            print(f"Error fetching plan history: {e}")
    
    return render_template('dashboard/dashboard.html', 
                         user=user, 
                         stripe_customer=stripe_customer,
                         plan_history=plan_history)

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
    
    # Get detailed plan history
    plan_history = None
    if stripe_customer:
        try:
            fetcher = UserPlanHistoryFetcher()
            plan_data = fetcher.get_comprehensive_plan_history(stripe_customer['stripe_id'])
            plan_history = plan_data if 'error' not in plan_data else None
            fetcher.close()
        except Exception as e:
            print(f"Error fetching plan history: {e}")
    
    return render_template('profile/profile.html', 
                         user=user,
                         stripe_customer=stripe_customer,
                         plan_history=plan_history)

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

# Include the original server routes for Stripe functionality
from server import app as stripe_app

# Copy the Stripe routes
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
            success_url=BASE_URL + '/success.html?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=BASE_URL + '/cancel.html',
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
        return jsonify({'error': 'No billing information found'}), 404
    
    try:
        # Create billing portal session
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer['stripe_id'],
            return_url=f"{BASE_URL}/profile"
        )
        
        return jsonify({'url': session.url})
    except Exception as e:
        print(f"Error creating billing portal session: {e}")
        return jsonify({'error': 'Unable to create billing portal session'}), 500

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

# ...existing code...

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
        print("✅ App users table ready")
    except Exception as e:
        print(f"⚠️  Warning: Could not create app_users table: {e}")
    finally:
        cur.close()
        conn.close()
    
    app.run(debug=True, port=4242)
