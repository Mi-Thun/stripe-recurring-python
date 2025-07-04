-- 0. Create app_users table if it doesn't exist
CREATE TABLE IF NOT EXISTS app_users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(256) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 1. Main Events Table - stores all raw Stripe events
CREATE TABLE stripe_events (
    id SERIAL PRIMARY KEY,
    stripe_event_id VARCHAR(255) UNIQUE NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    api_version VARCHAR(50),
    created_at TIMESTAMP NOT NULL,
    livemode BOOLEAN NOT NULL DEFAULT false,
    pending_webhooks INTEGER DEFAULT 0,
    request_id VARCHAR(255),
    raw_data JSONB NOT NULL, -- Complete event data
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processing_status VARCHAR(20) DEFAULT 'pending', -- pending, processed, failed
    error_message TEXT
);

-- 2. Customers Table
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    phone VARCHAR(50),
    address_line1 VARCHAR(255),
    address_line2 VARCHAR(255),
    address_city VARCHAR(100),
    address_state VARCHAR(100),
    address_postal_code VARCHAR(20),
    address_country CHAR(2),
    currency CHAR(3),
    balance INTEGER DEFAULT 0,
    tax_exempt VARCHAR(20) DEFAULT 'none',
    delinquent BOOLEAN DEFAULT false,
    invoice_prefix VARCHAR(50),
    preferred_locales JSONB,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Payment Methods Table
CREATE TABLE payment_methods (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT REFERENCES customers(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL, -- card, bank_account, etc.
    card_brand VARCHAR(20),
    card_last4 VARCHAR(4),
    card_exp_month INTEGER,
    card_exp_year INTEGER,
    card_country CHAR(2),
    card_funding VARCHAR(20), -- credit, debit, prepaid
    billing_details JSONB,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Products Table
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    active BOOLEAN DEFAULT true,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Prices Table
CREATE TABLE prices (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    product_id INT REFERENCES products(id) ON DELETE CASCADE,
    currency CHAR(3) NOT NULL,
    unit_amount INTEGER,
    billing_scheme VARCHAR(20) DEFAULT 'per_unit',
    recurring_interval VARCHAR(20), -- month, year, etc.
    recurring_interval_count INTEGER DEFAULT 1,
    lookup_key VARCHAR(255),
    nickname VARCHAR(255),
    active BOOLEAN DEFAULT true,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Subscriptions Table
CREATE TABLE subscriptions (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL, -- active, canceled, incomplete, etc.
    current_period_start TIMESTAMP,
    current_period_end TIMESTAMP,
    created_at_stripe TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    canceled_at TIMESTAMP,
    cancel_at_period_end BOOLEAN DEFAULT false,
    cancellation_reason VARCHAR(50),
    collection_method VARCHAR(50) DEFAULT 'charge_automatically',
    currency CHAR(3),
    default_payment_method_id INT REFERENCES payment_methods(id),
    trial_start TIMESTAMP,
    trial_end TIMESTAMP,
    billing_cycle_anchor TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Subscription Items Table
CREATE TABLE subscription_items (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    subscription_id INT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    price_id INT REFERENCES prices(id),
    quantity INTEGER DEFAULT 1,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Invoices Table
CREATE TABLE invoices (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    subscription_id INT REFERENCES subscriptions(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL, -- draft, open, paid, void, uncollectible
    billing_reason VARCHAR(50), -- subscription_create, subscription_cycle, etc.
    collection_method VARCHAR(50),
    currency CHAR(3) NOT NULL,
    amount_due INTEGER NOT NULL,
    amount_paid INTEGER NOT NULL,
    amount_remaining INTEGER NOT NULL,
    subtotal INTEGER NOT NULL,
    total INTEGER NOT NULL,
    tax_amount INTEGER DEFAULT 0,
    created_at_stripe TIMESTAMP NOT NULL,
    due_date TIMESTAMP,
    finalized_at TIMESTAMP,
    paid_at TIMESTAMP,
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    hosted_invoice_url TEXT,
    invoice_pdf TEXT,
    receipt_number VARCHAR(100),
    account_country CHAR(2),
    account_name VARCHAR(255),
    attempt_count INTEGER DEFAULT 0,
    attempted BOOLEAN DEFAULT false,
    auto_advance BOOLEAN DEFAULT false,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. Invoice Line Items Table
CREATE TABLE invoice_line_items (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    invoice_id INT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    subscription_id INT REFERENCES subscriptions(id),
    subscription_item_id INT REFERENCES subscription_items(id),
    price_id INT REFERENCES prices(id),
    type VARCHAR(50), -- subscription, invoiceitem
    amount INTEGER NOT NULL,
    currency CHAR(3) NOT NULL,
    description TEXT,
    quantity INTEGER DEFAULT 1,
    unit_amount INTEGER,
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    proration BOOLEAN DEFAULT false,
    proration_details JSONB,
    discountable BOOLEAN DEFAULT true,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. Charges Table
CREATE TABLE charges (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT REFERENCES customers(id) ON DELETE CASCADE,
    invoice_id INT REFERENCES invoices(id) ON DELETE SET NULL,
    payment_intent_id VARCHAR(255),
    payment_method_id INT REFERENCES payment_methods(id),
    amount INTEGER NOT NULL,
    amount_captured INTEGER DEFAULT 0,
    amount_refunded INTEGER DEFAULT 0,
    currency CHAR(3) NOT NULL,
    status VARCHAR(20) NOT NULL, -- succeeded, pending, failed
    paid BOOLEAN DEFAULT false,
    captured BOOLEAN DEFAULT false,
    refunded BOOLEAN DEFAULT false,
    disputed BOOLEAN DEFAULT false,
    failure_code VARCHAR(50),
    failure_message TEXT,
    outcome_type VARCHAR(50), -- authorized, manual_review, issuer_declined, etc.
    risk_level VARCHAR(20), -- normal, elevated, highest
    risk_score INTEGER,
    receipt_url TEXT,
    description TEXT,
    billing_details JSONB,
    payment_method_details JSONB,
    created_at_stripe TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 11. Payment Intents Table
CREATE TABLE payment_intents (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT REFERENCES customers(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    amount_capturable INTEGER DEFAULT 0,
    amount_received INTEGER DEFAULT 0,
    currency CHAR(3) NOT NULL,
    status VARCHAR(30) NOT NULL, -- requires_payment_method, succeeded, etc.
    confirmation_method VARCHAR(20),
    capture_method VARCHAR(20),
    payment_method_id INT REFERENCES payment_methods(id),
    setup_future_usage VARCHAR(20),
    description TEXT,
    client_secret VARCHAR(255),
    latest_charge_id VARCHAR(255),
    next_action JSONB,
    last_payment_error JSONB,
    created_at_stripe TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 12. Invoice Items Table (for one-time charges and prorations)
CREATE TABLE invoice_items (
    id SERIAL PRIMARY KEY,
    stripe_id VARCHAR(255) UNIQUE NOT NULL,
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    invoice_id INT REFERENCES invoices(id) ON DELETE CASCADE,
    subscription_id INT REFERENCES subscriptions(id),
    subscription_item_id INT REFERENCES subscription_items(id),
    price_id INT REFERENCES prices(id),
    amount INTEGER NOT NULL,
    currency CHAR(3) NOT NULL,
    description TEXT,
    quantity INTEGER DEFAULT 1,
    unit_amount INTEGER,
    period_start TIMESTAMP,
    period_end TIMESTAMP,
    proration BOOLEAN DEFAULT false,
    discountable BOOLEAN DEFAULT true,
    date_added TIMESTAMP,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 13. Event Processing Log Table
CREATE TABLE event_processing_log (
    id SERIAL PRIMARY KEY,
    stripe_event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    processing_started_at TIMESTAMP NOT NULL,
    processing_completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL, -- started, completed, failed, skipped
    error_message TEXT,
    objects_created JSONB, -- Track what was created/updated
    objects_updated JSONB,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes after table creation (PostgreSQL syntax)
CREATE INDEX idx_stripe_events_event_id ON stripe_events(stripe_event_id);
CREATE INDEX idx_stripe_events_type ON stripe_events(event_type);
CREATE INDEX idx_stripe_events_created ON stripe_events(created_at);

CREATE INDEX idx_customers_stripe_id ON customers(stripe_id);
CREATE INDEX idx_customers_email ON customers(email);

CREATE INDEX idx_payment_methods_customer ON payment_methods(customer_id);
CREATE INDEX idx_payment_methods_stripe_id ON payment_methods(stripe_id);

CREATE INDEX idx_products_stripe_id ON products(stripe_id);

CREATE INDEX idx_prices_stripe_id ON prices(stripe_id);
CREATE INDEX idx_prices_lookup_key ON prices(lookup_key);

CREATE INDEX idx_subscriptions_stripe_id ON subscriptions(stripe_id);
CREATE INDEX idx_subscriptions_customer ON subscriptions(customer_id);
CREATE INDEX idx_subscriptions_status ON subscriptions(status);

CREATE INDEX idx_subscription_items_stripe_id ON subscription_items(stripe_id);
CREATE INDEX idx_subscription_items_subscription ON subscription_items(subscription_id);

CREATE INDEX idx_invoices_stripe_id ON invoices(stripe_id);
CREATE INDEX idx_invoices_customer ON invoices(customer_id);
CREATE INDEX idx_invoices_subscription ON invoices(subscription_id);
CREATE INDEX idx_invoices_status ON invoices(status);

CREATE INDEX idx_invoice_line_items_stripe_id ON invoice_line_items(stripe_id);
CREATE INDEX idx_invoice_line_items_invoice ON invoice_line_items(invoice_id);
CREATE INDEX idx_invoice_line_items_subscription ON invoice_line_items(subscription_id);

CREATE INDEX idx_charges_stripe_id ON charges(stripe_id);
CREATE INDEX idx_charges_customer ON charges(customer_id);
CREATE INDEX idx_charges_status ON charges(status);

CREATE INDEX idx_payment_intents_stripe_id ON payment_intents(stripe_id);
CREATE INDEX idx_payment_intents_customer ON payment_intents(customer_id);
CREATE INDEX idx_payment_intents_status ON payment_intents(status);

CREATE INDEX idx_invoice_items_stripe_id ON invoice_items(stripe_id);
CREATE INDEX idx_invoice_items_customer ON invoice_items(customer_id);
CREATE INDEX idx_invoice_items_invoice ON invoice_items(invoice_id);

CREATE INDEX idx_event_log_stripe_event ON event_processing_log(stripe_event_id);
CREATE INDEX idx_event_log_status ON event_processing_log(status);

-- Views for easier querying
CREATE VIEW customer_subscription_summary AS
SELECT 
    c.id as customer_id,
    c.stripe_id as customer_stripe_id,
    c.email,
    c.name,
    s.id as subscription_id,
    s.stripe_id as subscription_stripe_id,
    s.status as subscription_status,
    s.current_period_start,
    s.current_period_end,
    p.stripe_id as price_stripe_id,
    p.lookup_key,
    p.unit_amount,
    p.currency,
    pr.name as product_name
FROM customers c
LEFT JOIN subscriptions s ON c.id = s.customer_id
LEFT JOIN subscription_items si ON s.id = si.subscription_id
LEFT JOIN prices p ON si.price_id = p.id
LEFT JOIN products pr ON p.product_id = pr.id
WHERE s.status IN ('active', 'trialing');
