#!/usr/bin/env python3
"""
Script to sync existing Stripe products and prices to database
This should be run when starting with an existing Stripe account
"""

import os
import stripe
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import json

load_dotenv()

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

def get_db_connection():
    return psycopg2.connect(os.getenv('PSQL_DB_URL'), cursor_factory=RealDictCursor)

def upsert_product(data, conn, cur):
    """Upsert product from Stripe"""
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
        print(f"Upserted product {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        print(f"Error upserting product {stripe_id}: {e}")
        raise

def upsert_price(data, conn, cur):
    """Upsert price from Stripe"""
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
        print(f"Upserted price {stripe_id} -> DB ID {result['id']}")
        return result['id']
        
    except Exception as e:
        print(f"Error upserting price {stripe_id}: {e}")
        raise

def sync_products_and_prices():
    """Sync all products and prices from Stripe to database"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        print("=== SYNCING PRODUCTS ===")
        products = stripe.Product.list(limit=100)
        product_count = 0
        
        for product in products.data:
            upsert_product(product, conn, cur)
            product_count += 1
        
        print(f"Synced {product_count} products")
        
        print("\n=== SYNCING PRICES ===")
        prices = stripe.Price.list(limit=100)
        price_count = 0
        
        for price in prices.data:
            upsert_price(price, conn, cur)
            price_count += 1
            
        print(f"Synced {price_count} prices")
        
        conn.commit()
        print("\n=== SYNC COMPLETE ===")
        print(f"Successfully synced {product_count} products and {price_count} prices")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during sync: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    sync_products_and_prices()
