#!/usr/bin/env python3
"""
Script to backfill subscription_items for existing subscriptions
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

def sync_subscription_items():
    """Fetch existing subscriptions from Stripe and ensure their items are in the database"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        print("=== SYNCING SUBSCRIPTION ITEMS ===")
        
        # Get all subscriptions from database that don't have items
        cur.execute("""
            SELECT s.stripe_id, s.id as db_id 
            FROM subscriptions s 
            LEFT JOIN subscription_items si ON s.id = si.subscription_id 
            WHERE si.id IS NULL
        """)
        
        missing_subscriptions = cur.fetchall()
        print(f"Found {len(missing_subscriptions)} subscriptions without items")
        
        for sub_record in missing_subscriptions:
            stripe_sub_id = sub_record['stripe_id']
            db_sub_id = sub_record['db_id']
            
            try:
                # Fetch the subscription from Stripe
                subscription = stripe.Subscription.retrieve(stripe_sub_id)
                print(f"Processing subscription {stripe_sub_id}")
                
                # Process subscription items
                items = subscription.get('items', {}).get('data', [])
                for item in items:
                    price_stripe_id = item.get('price', {}).get('id')
                    if price_stripe_id:
                        # Get price database ID
                        cur.execute("SELECT id FROM prices WHERE stripe_id = %s", (price_stripe_id,))
                        price_row = cur.fetchone()
                        if price_row:
                            price_db_id = price_row['id']
                            
                            # Insert subscription item
                            cur.execute("""
                                INSERT INTO subscription_items (
                                    stripe_id, subscription_id, price_id, quantity, metadata
                                ) VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (stripe_id) DO UPDATE SET
                                    price_id = EXCLUDED.price_id,
                                    quantity = EXCLUDED.quantity,
                                    metadata = EXCLUDED.metadata
                                RETURNING id;
                            """, (
                                item.get('id'),
                                db_sub_id,
                                price_db_id,
                                item.get('quantity', 1),
                                json.dumps(item.get('metadata', {}))
                            ))
                            
                            result = cur.fetchone()
                            print(f"  Added subscription item {item.get('id')} -> DB ID {result['id']}")
                        else:
                            print(f"  Warning: Price {price_stripe_id} not found in database")
                    else:
                        print(f"  Warning: Item {item.get('id')} has no price")
                        
            except Exception as e:
                print(f"Error processing subscription {stripe_sub_id}: {e}")
                continue
        
        conn.commit()
        print("=== SYNC COMPLETE ===")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during sync: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    sync_subscription_items()
