import os
import json
from flask import Flask, request, redirect, jsonify, current_app
from dotenv import load_dotenv
import stripe
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime
import logging
from logging_config import StripeIntegrationLoggerSetup, log_system_info, log_startup_banner

load_dotenv()

log_setup = StripeIntegrationLoggerSetup(log_level=logging.DEBUG)
loggers = log_setup.setup_all_loggers()
logger = loggers['main']
db_logger = loggers['database']

log_startup_banner(logger)
log_system_info(logger)

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')
BASE_URL = os.getenv('BASE_URL')
app = Flask(__name__, static_url_path='', static_folder='public')

def get_db_connection():
    return psycopg2.connect(os.getenv('PSQL_DB_URL'), cursor_factory=RealDictCursor)

def get_current_user_id():
    return 'mithun@sgcsoft.net'

def load_latest_subscription_for_user(email):
    """Load latest subscription for user with proper SQL query"""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Query subscriptions by customer email through customers table
        cur.execute("""
            SELECT s.*, c.email, p.lookup_key, p.unit_amount, pr.name as product_name
            FROM subscriptions s
            JOIN customers c ON s.customer_id = c.id
            LEFT JOIN subscription_items si ON s.id = si.subscription_id
            LEFT JOIN prices p ON si.price_id = p.id
            LEFT JOIN products pr ON p.product_id = pr.id
            WHERE c.email = %s 
            ORDER BY s.created_at DESC 
            LIMIT 1;
        """, (email,))
        row = cur.fetchone()
        if row:
            return dict(row)
        else:
            return {}
    except Exception as e:
        logger.error(f"Error loading subscription for user {email}: {e}")
        return {}
    finally:
        cur.close()
        conn.close()
        
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
    

def upsert_payment_intent(data, conn, cur):
        """Upsert payment intent data from Stripe webhook with logging"""
        stripe_id = data.get('id')
        customer_stripe_id = data.get('customer')
        amount = data.get('amount', 0)
        amount_capturable = data.get('amount_capturable', 0)
        amount_received = data.get('amount_received', 0)
        currency = data.get('currency', '').upper()
        status = data.get('status', '')
        confirmation_method = data.get('confirmation_method')
        capture_method = data.get('capture_method')
        payment_method_stripe_id = data.get('payment_method')
        setup_future_usage = data.get('setup_future_usage')
        description = data.get('description')
        client_secret = data.get('client_secret')
        latest_charge_id = data.get('latest_charge')
        next_action = data.get('next_action')
        last_payment_error = data.get('last_payment_error')
        created_at_stripe = datetime.datetime.fromtimestamp(data.get('created', 0))

        # Lookup customer_id from customers table
        customer_id = None
        if customer_stripe_id:
            cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
            row = cur.fetchone()
            if row:
                customer_id = row['id']

        # Lookup payment_method_id from payment_methods table
        payment_method_id = None
        if payment_method_stripe_id:
            cur.execute("SELECT id FROM payment_methods WHERE stripe_id = %s", (payment_method_stripe_id,))
            row = cur.fetchone()
            if row:
                payment_method_id = row['id']

        try:
            db_logger.info(f"Upserting payment_intent {stripe_id} for customer {customer_id}")
            cur.execute("""
                INSERT INTO payment_intents (
                    stripe_id, customer_id, amount, amount_capturable, amount_received,
                    currency, status, confirmation_method, capture_method, payment_method_id,
                    setup_future_usage, description, client_secret, latest_charge_id,
                    next_action, last_payment_error, created_at_stripe
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (stripe_id) DO UPDATE SET
                    customer_id = EXCLUDED.customer_id,
                    amount = EXCLUDED.amount,
                    amount_capturable = EXCLUDED.amount_capturable,
                    amount_received = EXCLUDED.amount_received,
                    currency = EXCLUDED.currency,
                    status = EXCLUDED.status,
                    confirmation_method = EXCLUDED.confirmation_method,
                    capture_method = EXCLUDED.capture_method,
                    payment_method_id = EXCLUDED.payment_method_id,
                    setup_future_usage = EXCLUDED.setup_future_usage,
                    description = EXCLUDED.description,
                    client_secret = EXCLUDED.client_secret,
                    latest_charge_id = EXCLUDED.latest_charge_id,
                    next_action = EXCLUDED.next_action,
                    last_payment_error = EXCLUDED.last_payment_error,
                    created_at_stripe = EXCLUDED.created_at_stripe,
                    created_at = CURRENT_TIMESTAMP
                RETURNING id;
            """, (
                stripe_id, customer_id, amount, amount_capturable, amount_received,
                currency, status, confirmation_method, capture_method, payment_method_id,
                setup_future_usage, description, client_secret, latest_charge_id,
                json.dumps(next_action) if next_action else None,
                json.dumps(last_payment_error) if last_payment_error else None,
                created_at_stripe
            ))
            result = cur.fetchone()
            db_id = result['id']
            db_logger.info(f"PaymentIntent upserted: Stripe ID {stripe_id} -> DB ID {db_id}")
            return db_id
        except psycopg2.Error as e:
            db_logger.error(f"PostgreSQL error upserting payment_intent {stripe_id}: {e}")
            raise
        except Exception as e:
            db_logger.error(f"Unexpected error upserting payment_intent {stripe_id}: {e}")
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
            ) VALUES (%s, %s, %s, TO_TIMESTAMP(%s), %s, %s, %s, %s, 'pending')
            ON CONFLICT (stripe_event_id) DO UPDATE SET
                processing_status = 'duplicate'
            RETURNING id;
        """, (
            event_id,
            event_type,
            event.get('api_version', ''),
            event['created'],
            event.get('livemode', False),
            event.get('pending_webhooks', 0),
            event.get('request', {}).get('id', ''),
            json.dumps(event)
        ))
        
        result = cur.fetchone()
        if result:
            db_logger.info(f"Stored event {event_id} with ID {result['id']}")
            return result['id']
        else:
            logger.warning(f"Event {event_id} was duplicate")
            return None
            
    except Exception as e:
        db_logger.error(f"Error storing event {event_id}: {e}")
        raise

def upsert_payment_method(data, conn, cur):
    """Upsert payment method from Stripe event"""
    pm_id = data.get('id')
    customer_stripe_id = data.get('customer')
    
    # Get customer database ID
    customer_id = None
    if customer_stripe_id:
        cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
        row = cur.fetchone()
        if row:
            customer_id = row['id']
    
    try:
        card = data.get('card', {}) or {}
        billing = data.get('billing_details', {}) or {}
        
        cur.execute("""
            INSERT INTO payment_methods (
                stripe_id, customer_id, type, card_brand, card_last4,
                card_exp_month, card_exp_year, card_country, card_funding,
                billing_details, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                customer_id = EXCLUDED.customer_id,
                type = EXCLUDED.type,
                card_brand = EXCLUDED.card_brand,
                card_last4 = EXCLUDED.card_last4,
                card_exp_month = EXCLUDED.card_exp_month,
                card_exp_year = EXCLUDED.card_exp_year,
                card_country = EXCLUDED.card_country,
                card_funding = EXCLUDED.card_funding,
                billing_details = EXCLUDED.billing_details,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            pm_id,
            customer_id,
            data.get('type', ''),
            card.get('brand', ''),
            card.get('last4', ''),
            card.get('exp_month'),
            card.get('exp_year'),
            card.get('country', ''),
            card.get('funding', ''),
            json.dumps(billing),
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted payment method {pm_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting payment method {pm_id}: {e}")
        raise

def upsert_charge(data, conn, cur):
    """Upsert charge from Stripe event"""
    stripe_id = data.get('id')
    customer_stripe_id = data.get('customer')
    payment_intent_id = data.get('payment_intent')
    payment_method_stripe_id = data.get('payment_method')
    invoice_stripe_id = data.get('invoice')
    
    # Get foreign key IDs
    customer_id = None
    if customer_stripe_id:
        cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
        row = cur.fetchone()
        if row:
            customer_id = row['id']
    
    payment_method_id = None
    if payment_method_stripe_id:
        cur.execute("SELECT id FROM payment_methods WHERE stripe_id = %s", (payment_method_stripe_id,))
        row = cur.fetchone()
        if row:
            payment_method_id = row['id']
    
    invoice_id = None
    if invoice_stripe_id:
        cur.execute("SELECT id FROM invoices WHERE stripe_id = %s", (invoice_stripe_id,))
        row = cur.fetchone()
        if row:
            invoice_id = row['id']
    
    try:
        outcome = data.get('outcome', {}) or {}
        billing_details = data.get('billing_details', {}) or {}
        payment_method_details = data.get('payment_method_details', {}) or {}
        
        cur.execute("""
            INSERT INTO charges (
                stripe_id, customer_id, invoice_id, payment_intent_id, payment_method_id,
                amount, amount_captured, amount_refunded, currency, status,
                paid, captured, refunded, disputed, failure_code, failure_message,
                outcome_type, risk_level, risk_score, receipt_url, description,
                billing_details, payment_method_details, created_at_stripe
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, TO_TIMESTAMP(%s)
            )
            ON CONFLICT (stripe_id) DO UPDATE SET
                status = EXCLUDED.status,
                amount_captured = EXCLUDED.amount_captured,
                amount_refunded = EXCLUDED.amount_refunded,
                paid = EXCLUDED.paid,
                captured = EXCLUDED.captured,
                refunded = EXCLUDED.refunded,
                disputed = EXCLUDED.disputed,
                failure_code = EXCLUDED.failure_code,
                failure_message = EXCLUDED.failure_message,
                outcome_type = EXCLUDED.outcome_type,
                risk_level = EXCLUDED.risk_level,
                risk_score = EXCLUDED.risk_score,
                receipt_url = EXCLUDED.receipt_url
            RETURNING id;
        """, (
            stripe_id, customer_id, invoice_id, payment_intent_id, payment_method_id,
            data.get('amount', 0), data.get('amount_captured', 0), data.get('amount_refunded', 0),
            data.get('currency', '').upper(), data.get('status', ''),
            data.get('paid', False), data.get('captured', False), data.get('refunded', False),
            data.get('disputed', False), data.get('failure_code'), data.get('failure_message'),
            outcome.get('type'), outcome.get('risk_level'), outcome.get('risk_score'),
            data.get('receipt_url'), data.get('description'),
            json.dumps(billing_details), json.dumps(payment_method_details),
            data.get('created', 0)
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted charge {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting charge {stripe_id}: {e}")
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

def upsert_invoice_line_item(data, conn, cur):
    """Upsert invoice line item from Stripe event"""
    stripe_id = data.get('id')
    invoice_stripe_id = data.get('invoice')
    subscription_stripe_id = data.get('subscription')
    subscription_item_stripe_id = data.get('subscription_item')
    price_stripe_id = data.get('price', {}).get('id') if data.get('price') else None
    
    # Get foreign key IDs
    invoice_id = None
    if invoice_stripe_id:
        cur.execute("SELECT id FROM invoices WHERE stripe_id = %s", (invoice_stripe_id,))
        row = cur.fetchone()
        if row:
            invoice_id = row['id']
    
    subscription_id = None
    if subscription_stripe_id:
        cur.execute("SELECT id FROM subscriptions WHERE stripe_id = %s", (subscription_stripe_id,))
        row = cur.fetchone()
        if row:
            subscription_id = row['id']
    
    subscription_item_id = None
    if subscription_item_stripe_id:
        cur.execute("SELECT id FROM subscription_items WHERE stripe_id = %s", (subscription_item_stripe_id,))
        row = cur.fetchone()
        if row:
            subscription_item_id = row['id']
    
    price_id = None
    if price_stripe_id:
        cur.execute("SELECT id FROM prices WHERE stripe_id = %s", (price_stripe_id,))
        row = cur.fetchone()
        if row:
            price_id = row['id']
    
    try:
        period = data.get('period', {}) or {}
        proration_details = data.get('proration_details', {}) or {}
        
        cur.execute("""
            INSERT INTO invoice_line_items (
                stripe_id, invoice_id, subscription_id, subscription_item_id, price_id,
                type, amount, currency, description, quantity, unit_amount,
                period_start, period_end, proration, proration_details,
                discountable, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency,
                description = EXCLUDED.description,
                quantity = EXCLUDED.quantity,
                unit_amount = EXCLUDED.unit_amount,
                period_start = EXCLUDED.period_start,
                period_end = EXCLUDED.period_end,
                proration = EXCLUDED.proration,
                proration_details = EXCLUDED.proration_details,
                discountable = EXCLUDED.discountable,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            stripe_id, invoice_id, subscription_id, subscription_item_id, price_id,
            data.get('type', ''), data.get('amount', 0), data.get('currency', '').upper(),
            data.get('description', ''), data.get('quantity', 1), data.get('unit_amount'),
            datetime.datetime.fromtimestamp(period.get('start', 0)) if period.get('start') else None,
            datetime.datetime.fromtimestamp(period.get('end', 0)) if period.get('end') else None,
            data.get('proration', False), json.dumps(proration_details),
            data.get('discountable', True), json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted invoice line item {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting invoice line item {stripe_id}: {e}")
        raise

def upsert_invoice_item(data, conn, cur):
    """Upsert invoice item (one-time charges) from Stripe event"""
    stripe_id = data.get('id')
    customer_stripe_id = data.get('customer')
    invoice_stripe_id = data.get('invoice')
    subscription_stripe_id = data.get('subscription')
    subscription_item_stripe_id = data.get('subscription_item')
    price_stripe_id = data.get('price', {}).get('id') if data.get('price') else None
    
    # Get foreign key IDs
    customer_id = None
    if customer_stripe_id:
        cur.execute("SELECT id FROM customers WHERE stripe_id = %s", (customer_stripe_id,))
        row = cur.fetchone()
        if row:
            customer_id = row['id']
    
    invoice_id = None
    if invoice_stripe_id:
        cur.execute("SELECT id FROM invoices WHERE stripe_id = %s", (invoice_stripe_id,))
        row = cur.fetchone()
        if row:
            invoice_id = row['id']
    
    subscription_id = None
    if subscription_stripe_id:
        cur.execute("SELECT id FROM subscriptions WHERE stripe_id = %s", (subscription_stripe_id,))
        row = cur.fetchone()
        if row:
            subscription_id = row['id']
    
    subscription_item_id = None
    if subscription_item_stripe_id:
        cur.execute("SELECT id FROM subscription_items WHERE stripe_id = %s", (subscription_item_stripe_id,))
        row = cur.fetchone()
        if row:
            subscription_item_id = row['id']
    
    price_id = None
    if price_stripe_id:
        cur.execute("SELECT id FROM prices WHERE stripe_id = %s", (price_stripe_id,))
        row = cur.fetchone()
        if row:
            price_id = row['id']
    
    try:
        period = data.get('period', {}) or {}
        
        cur.execute("""
            INSERT INTO invoice_items (
                stripe_id, customer_id, invoice_id, subscription_id, subscription_item_id,
                price_id, amount, currency, description, quantity, unit_amount,
                period_start, period_end, proration, discountable, date_added, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (stripe_id) DO UPDATE SET
                invoice_id = EXCLUDED.invoice_id,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency,
                description = EXCLUDED.description,
                quantity = EXCLUDED.quantity,
                unit_amount = EXCLUDED.unit_amount,
                period_start = EXCLUDED.period_start,
                period_end = EXCLUDED.period_end,
                proration = EXCLUDED.proration,
                discountable = EXCLUDED.discountable,
                date_added = EXCLUDED.date_added,
                metadata = EXCLUDED.metadata
            RETURNING id;
        """, (
            stripe_id, customer_id, invoice_id, subscription_id, subscription_item_id,
            price_id, data.get('amount', 0), data.get('currency', '').upper(),
            data.get('description', ''), data.get('quantity', 1), data.get('unit_amount'),
            datetime.datetime.fromtimestamp(period.get('start', 0)) if period.get('start') else None,
            datetime.datetime.fromtimestamp(period.get('end', 0)) if period.get('end') else None,
            data.get('proration', False), data.get('discountable', True),
            datetime.datetime.fromtimestamp(data.get('date', 0)) if data.get('date') else None,
            json.dumps(data.get('metadata', {}))
        ))
        
        result = cur.fetchone()
        db_logger.info(f"Upserted invoice item {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        db_logger.error(f"Error upserting invoice item {stripe_id}: {e}")
        raise

def log_event_processing(stripe_event_id, event_type, status, conn, cur, error_message=None, objects_created=None, objects_updated=None):
    """Log event processing status to event_processing_log table"""
    try:
        if status == 'started':
            cur.execute("""
                INSERT INTO event_processing_log (
                    stripe_event_id, event_type, processing_started_at, status
                ) VALUES (%s, %s, CURRENT_TIMESTAMP, %s)
                RETURNING id;
            """, (stripe_event_id, event_type, status))
        elif status in ['completed', 'failed', 'skipped']:
            cur.execute("""
                UPDATE event_processing_log 
                SET processing_completed_at = CURRENT_TIMESTAMP,
                    status = %s,
                    error_message = %s,
                    objects_created = %s,
                    objects_updated = %s
                WHERE stripe_event_id = %s AND event_type = %s;
            """, (
                status, error_message,
                json.dumps(objects_created) if objects_created else None,
                json.dumps(objects_updated) if objects_updated else None,
                stripe_event_id, event_type
            ))
        
        if status == 'started':
            result = cur.fetchone()
            return result['id'] if result else None
            
    except Exception as e:
        db_logger.error(f"Error logging event processing for {stripe_event_id}: {e}")

def upsert_checkout_session(data, conn, cur):
    """Process checkout session completion"""
    session_id = data.get('id')
    customer_stripe_id = data.get('customer')
    subscription_stripe_id = data.get('subscription')
    
    try:
        # If customer exists, update or create
        if customer_stripe_id:
            customer = stripe.Customer.retrieve(customer_stripe_id)
            upsert_customer(customer, conn, cur)
        
        # If subscription exists, update or create
        if subscription_stripe_id:
            subscription = stripe.Subscription.retrieve(subscription_stripe_id)
            upsert_subscription_new(subscription, conn, cur)
        
        db_logger.info(f"Processed checkout session {session_id}")
        
    except Exception as e:
        db_logger.error(f"Error processing checkout session {session_id}: {e}")
        raise

# Flask Routes
@app.route('/')
def index():
    return current_app.send_static_file('index.html')

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
        print(f"Error fetching products: {e}")
        return jsonify({"error": "Failed to fetch products"}), 500

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        lookup_key = request.form.get('lookup_key')
        user_id = get_current_user_id()
        sub = load_latest_subscription_for_user(user_id)
        if sub.get('status') == 'active':
            return redirect('/already_subscribed.html')
        prices = stripe.Price.list(lookup_keys=[lookup_key], expand=['data.product'])
        price = prices.data[0]
        session = stripe.checkout.Session.create(
            line_items=[{"price": price.id, "quantity": 1}],
            mode="subscription",
            client_reference_id='cus_SaUKdQaGcnYTfa',
            success_url=f"{BASE_URL}/success.html?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/cancel.html",
        )
        return redirect(session.url, code=303)
    except Exception as e:
        print(f"Checkout session error: {e}")
        return "Server error", 500

@app.route('/create-portal-session', methods=['POST'])
def customer_portal():
    try:
        session_id = request.form.get('session_id')
        session = stripe.checkout.Session.retrieve(session_id)
        portal_session = stripe.billing_portal.Session.create(
            customer=session.customer,
            return_url=BASE_URL
        )
        return redirect(portal_session.url, code=303)
    except Exception as e:
        print(f"Portal session error: {e}")
        return jsonify({"error": "Portal session failed"}), 500

@app.route('/webhook', methods=['POST'])
def webhook_received():
    try:
        payload = request.data
        sig_header = request.headers.get('stripe-signature')

        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)

        event_type = event['type']
        data = event['data']['object']
        
        # Process all events with comprehensive database operations
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            # Store the raw event first
            store_stripe_event(event, conn, cur)
            
            # Log event processing start
            log_event_processing(event.get('id'), event_type, 'started', conn, cur)
            
            # Process events based on type
            if event_type == 'customer.created':
                upsert_customer(data, conn, cur)
                logger.info(f"Processed customer.created: {data.get('id')}")
                
            elif event_type == 'customer.updated':
                upsert_customer(data, conn, cur)
                logger.info(f"Processed customer.updated: {data.get('id')}")
                
            elif event_type == 'product.created':
                upsert_product(data, conn, cur)
                logger.info(f"Processed product.created: {data.get('id')}")
                
            elif event_type == 'product.updated':
                upsert_product(data, conn, cur)
                logger.info(f"Processed product.updated: {data.get('id')}")
                
            elif event_type == 'price.created':
                upsert_price(data, conn, cur)
                logger.info(f"Processed price.created: {data.get('id')}")
                
            elif event_type == 'price.updated':
                upsert_price(data, conn, cur)
                logger.info(f"Processed price.updated: {data.get('id')}")
                
            elif event_type == 'payment_intent.created':
                upsert_payment_intent(data, conn, cur)
                logger.info(f"Processed payment_intent.created: {data.get('id')}")
                
            elif event_type == 'payment_intent.succeeded':
                upsert_payment_intent(data, conn, cur)
                logger.info(f"Processed payment_intent.succeeded: {data.get('id')}")
                
            elif event_type == 'charge.succeeded':
                upsert_charge(data, conn, cur)
                logger.info(f"Processed charge.succeeded: {data.get('id')}")
                
            elif event_type == 'payment_method.attached':
                upsert_payment_method(data, conn, cur)
                logger.info(f"Processed payment_method.attached: {data.get('id')}")
                
            elif event_type == 'invoice.created':
                upsert_invoice_new(data, conn, cur)
                # Also process line items if present
                if data.get('lines', {}).get('data'):
                    for line_item in data['lines']['data']:
                        upsert_invoice_line_item(line_item, conn, cur)
                logger.info(f"Processed invoice.created: {data.get('id')}")
                
            elif event_type == 'invoice.finalized':
                upsert_invoice_new(data, conn, cur)
                # Also process line items if present
                if data.get('lines', {}).get('data'):
                    for line_item in data['lines']['data']:
                        upsert_invoice_line_item(line_item, conn, cur)
                logger.info(f"Processed invoice.finalized: {data.get('id')}")
                
            elif event_type == 'invoice.paid':
                upsert_invoice_new(data, conn, cur)
                logger.info(f"Processed invoice.paid: {data.get('id')}")
                
            elif event_type == 'invoice.payment_succeeded':
                upsert_invoice_new(data, conn, cur)
                logger.info(f"Processed invoice.payment_succeeded: {data.get('id')}")
                
            elif event_type == 'invoiceitem.created':
                upsert_invoice_item(data, conn, cur)
                logger.info(f"Processed invoiceitem.created: {data.get('id')}")
                
            elif event_type == 'invoiceitem.updated':
                upsert_invoice_item(data, conn, cur)
                logger.info(f"Processed invoiceitem.updated: {data.get('id')}")
                
            elif event_type == 'invoiceitem.deleted':
                # Log the deletion but don't process (item is already deleted)
                logger.info(f"Processed invoiceitem.deleted: {data.get('id')}")
                
            elif event_type == 'customer.subscription.created':
                upsert_subscription_new(data, conn, cur)
                logger.info(f"Processed customer.subscription.created: {data.get('id')}")
                
            elif event_type == 'customer.subscription.updated':
                upsert_subscription_new(data, conn, cur)
                logger.info(f"Processed customer.subscription.updated: {data.get('id')}")
                
            elif event_type == 'customer.subscription.deleted':
                upsert_subscription_new(data, conn, cur)
                logger.info(f"Processed customer.subscription.deleted: {data.get('id')}")
                
            elif event_type == 'checkout.session.completed':
                upsert_checkout_session(data, conn, cur)
                logger.info(f"Processed checkout.session.completed: {data.get('id')}")
                
            else:
                logger.warning(f"Unhandled event type: {event_type}")
            
            conn.commit()
            log_event_processing(event.get('id'), event_type, 'completed', conn, cur)
            logger.info(f"Successfully processed webhook event: {event_type} - {event.get('id')}")
            
        except Exception as e:
            conn.rollback()
            log_event_processing(event.get('id'), event_type, 'failed', conn, cur, error_message=str(e))
            logger.error(f"Error processing webhook {event_type}: {e}")
            raise
            
        finally:
            cur.close()
            conn.close()

        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': 'Webhook error'}), 400

@app.route('/cancel-subscription', methods=['POST'])
def cancel_subscription():
    user_id = get_current_user_id()
    sub = load_latest_subscription_for_user(user_id)
    if not sub or 'id' not in sub:
        return jsonify({'error': 'No active subscription found'}), 400
    try:
        stripe.Subscription.delete(sub['id'])
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM subscriptions WHERE customer_id = %s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        return redirect('/cancel.html?reason=cancel_subscription')
    except Exception as e:
        print(f"Cancel subscription error: {e}")
        return jsonify({'error': 'Failed to cancel'}), 500

@app.route('/update-subscription', methods=['POST'])
def update_subscription():
    user_id = get_current_user_id()
    lookup_key = request.form.get('lookup_key')
    sub = load_latest_subscription_for_user(user_id)
    if not sub or 'id' not in sub:
        return jsonify({'error': 'No active subscription'}), 400
    try:
        prices = stripe.Price.list(lookup_keys=[lookup_key], expand=['data.product'])
        new_price_id = prices.data[0].id
        subscription = stripe.Subscription.retrieve(sub['id'])
        item_id = subscription['items']['data'][0]['id']
        stripe.Subscription.modify(
            sub['id'],
            cancel_at_period_end=False,
            proration_behavior='create_prorations',
            items=[{'id': item_id, 'price': new_price_id}]
        )
        return redirect('/success.html')
    except Exception as e:
        print(f"Update subscription error: {e}")
        return jsonify({'error': 'Update failed'}), 500

@app.route('/my-subscription', methods=['GET'])
def my_subscription():
    user_id = get_current_user_id()
    sub = load_latest_subscription_for_user(user_id)
    if sub and sub.get('status') == 'active':
        return jsonify({
            "has_active": True,
            "plan": sub.get('lookup_key'),
            "price_id": sub.get('unit_amount'),
            "stripe_subscription_id": sub.get('stripe_id'),
            "status": sub.get('status'),
            "product_name": sub.get('product_name')
        })
    else:
        return jsonify({"has_active": False})


if __name__ == '__main__':
    app.run(port=4242, debug=True)