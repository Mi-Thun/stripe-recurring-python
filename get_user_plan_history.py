#!/usr/bin/env python3
"""
Fetch All Plan Data for a User by Stripe Customer ID

This script provides comprehensive plan history for a user, including:
- Current active subscription plans
- Previous/historical subscription plans
- Plan details (name, price, billing frequency)
- Subscription status changes over time
- Invoice history with plan information

Usage:
    python get_user_plan_history.py <stripe_customer_id>
    python get_user_plan_history.py --email <customer_email>
"""

import os
import sys
import json
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any
import psycopg2
from psycopg2.extras import RealDictCursor
import stripe
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure Stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

# Database configuration
DATABASE_URL = os.getenv('PSQL_DB_URL')

class UserPlanHistoryFetcher:
    def __init__(self):
        self.conn = None
        self.setup_database_connection()
    
    def setup_database_connection(self):
        """Setup database connection"""
        try:
            self.conn = psycopg2.connect(DATABASE_URL)
            print("✅ Database connection established")
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            sys.exit(1)
    
    def get_customer_by_email(self, email: str) -> Optional[Dict]:
        """Get customer information by email"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT id, stripe_id, email, name, created_at
                FROM customers 
                WHERE email = %s
            """, (email,))
            result = cursor.fetchone()
            return dict(result) if result else None
    
    def get_customer_by_stripe_id(self, stripe_customer_id: str) -> Optional[Dict]:
        """Get customer information by Stripe ID"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT id, stripe_id, email, name, created_at
                FROM customers 
                WHERE stripe_id = %s
            """, (stripe_customer_id,))
            result = cursor.fetchone()
            return dict(result) if result else None
    
    def get_all_subscriptions_for_customer(self, customer_db_id: int) -> List[Dict]:
        """Get all subscriptions (current and historical) for a customer"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    s.id,
                    s.stripe_id,
                    s.status,
                    s.current_period_start,
                    s.current_period_end,
                    s.created_at_stripe,
                    s.started_at,
                    s.ended_at,
                    s.canceled_at,
                    s.cancel_at_period_end,
                    s.cancellation_reason,
                    s.collection_method,
                    s.trial_start,
                    s.trial_end,
                    s.metadata
                FROM subscriptions s
                WHERE s.customer_id = %s
                ORDER BY s.created_at_stripe DESC
            """, (customer_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_subscription_items_for_subscription(self, subscription_db_id: int) -> List[Dict]:
        """Get all subscription items (plans) for a specific subscription"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    si.id,
                    si.stripe_id,
                    si.quantity,
                    si.metadata as item_metadata,
                    p.stripe_id as price_stripe_id,
                    p.unit_amount,
                    p.currency,
                    p.billing_scheme,
                    p.recurring_interval,
                    p.recurring_interval_count,
                    p.lookup_key,
                    p.nickname as price_nickname,
                    p.metadata as price_metadata,
                    pr.stripe_id as product_stripe_id,
                    pr.name as product_name,
                    pr.description as product_description,
                    pr.metadata as product_metadata
                FROM subscription_items si
                LEFT JOIN prices p ON si.price_id = p.id
                LEFT JOIN products pr ON p.product_id = pr.id
                WHERE si.subscription_id = %s
                ORDER BY si.id
            """, (subscription_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_invoice_history_for_customer(self, customer_db_id: int) -> List[Dict]:
        """Get invoice history with plan information"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    i.id,
                    i.stripe_id,
                    i.status,
                    i.amount_due,
                    i.amount_paid,
                    i.currency,
                    i.period_start,
                    i.period_end,
                    i.created_at_stripe,
                    i.paid_at,
                    s.stripe_id as subscription_stripe_id,
                    s.status as subscription_status
                FROM invoices i
                LEFT JOIN subscriptions s ON i.subscription_id = s.id
                WHERE i.customer_id = %s
                ORDER BY i.created_at_stripe DESC
            """, (customer_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_invoice_line_items_for_invoice(self, invoice_db_id: int) -> List[Dict]:
        """Get line items for a specific invoice"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    ili.id,
                    ili.stripe_id,
                    ili.amount,
                    ili.currency,
                    ili.description,
                    ili.period_start,
                    ili.period_end,
                    ili.quantity,
                    p.stripe_id as price_stripe_id,
                    p.unit_amount,
                    p.recurring_interval,
                    p.lookup_key,
                    p.nickname as price_nickname,
                    pr.stripe_id as product_stripe_id,
                    pr.name as product_name,
                    pr.description as product_description
                FROM invoice_line_items ili
                LEFT JOIN prices p ON ili.price_id = p.id
                LEFT JOIN products pr ON p.product_id = pr.id
                WHERE ili.invoice_id = %s
                ORDER BY ili.id
            """, (invoice_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_invoice_items_for_customer(self, customer_db_id: int) -> List[Dict]:
        """Get all invoice items (including prorations) for a customer"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    ii.id,
                    ii.stripe_id,
                    ii.amount,
                    ii.currency,
                    ii.description,
                    ii.proration,
                    ii.period_start,
                    ii.period_end,
                    ii.created_at,
                    p.stripe_id as price_stripe_id,
                    p.unit_amount,
                    p.recurring_interval,
                    p.lookup_key,
                    p.nickname as price_nickname,
                    pr.stripe_id as product_stripe_id,
                    pr.name as product_name,
                    pr.description as product_description,
                    i.stripe_id as invoice_stripe_id,
                    s.stripe_id as subscription_stripe_id
                FROM invoice_items ii
                LEFT JOIN invoices i ON ii.invoice_id = i.id
                LEFT JOIN subscriptions s ON ii.subscription_id = s.id
                LEFT JOIN prices p ON ii.price_id = p.id
                LEFT JOIN products pr ON p.product_id = pr.id
                WHERE ii.customer_id = %s
                ORDER BY ii.created_at DESC
            """, (customer_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def enrich_with_stripe_data(self, customer_stripe_id: str) -> Dict:
        """Fetch additional data from Stripe API if needed"""
        try:
            # Get customer from Stripe
            stripe_customer = stripe.Customer.retrieve(customer_stripe_id)
            
            # Get subscription history from Stripe (includes deleted subscriptions)
            stripe_subscriptions = stripe.Subscription.list(
                customer=customer_stripe_id,
                limit=100,
                status='all'
            )
            
            return {
                'stripe_customer': stripe_customer,
                'stripe_subscriptions': stripe_subscriptions.data
            }
        except Exception as e:
            print(f"⚠️  Warning: Could not fetch additional Stripe data: {e}")
            return {}
    
    def format_currency(self, amount: int, currency: str) -> str:
        """Format currency amount"""
        if amount is None:
            return "N/A"
        return f"{amount / 100:.2f} {currency.upper()}"
    
    def format_billing_frequency(self, interval: str, interval_count: int) -> str:
        """Format billing frequency"""
        if not interval:
            return "One-time"
        
        if interval_count == 1:
            return f"Every {interval}"
        else:
            return f"Every {interval_count} {interval}s"
    
    def get_comprehensive_plan_history(self, customer_identifier: str, is_email: bool = False) -> Dict:
        """Get comprehensive plan history for a customer"""
        print(f"🔍 Fetching plan history for {'email' if is_email else 'Stripe customer ID'}: {customer_identifier}")
        
        # Get customer info
        if is_email:
            customer = self.get_customer_by_email(customer_identifier)
            if not customer:
                return {"error": f"Customer not found with email: {customer_identifier}"}
        else:
            customer = self.get_customer_by_stripe_id(customer_identifier)
            if not customer:
                return {"error": f"Customer not found with Stripe ID: {customer_identifier}"}
        
        customer_db_id = customer['id']
        customer_stripe_id = customer['stripe_id']
        
        print(f"📋 Found customer: {customer['name']} ({customer['email']})")
        
        # Get all subscriptions
        subscriptions = self.get_all_subscriptions_for_customer(customer_db_id)
        print(f"📊 Found {len(subscriptions)} subscription(s)")
        
        # Get subscription details with plans
        detailed_subscriptions = []
        for sub in subscriptions:
            subscription_items = self.get_subscription_items_for_subscription(sub['id'])
            sub['plans'] = subscription_items
            detailed_subscriptions.append(sub)
        
        # Get invoice history
        invoices = self.get_invoice_history_for_customer(customer_db_id)
        print(f"🧾 Found {len(invoices)} invoice(s)")
        
        # Get invoice line items
        detailed_invoices = []
        for invoice in invoices:
            line_items = self.get_invoice_line_items_for_invoice(invoice['id'])
            invoice['line_items'] = line_items
            detailed_invoices.append(invoice)
        
        # Get additional Stripe data
        stripe_data = self.enrich_with_stripe_data(customer_stripe_id)
        
        # Get subscription change events
        events = self.get_subscription_change_events(customer_db_id)
        plan_changes = self.get_plan_changes_from_events(events)
        print(f"📅 Found {len(plan_changes)} plan change event(s)")
        
        # Get historical plans from invoices
        historical_plans = self.get_historical_plans_from_invoices(customer_db_id)
        print(f"📋 Found {len(historical_plans)} historical plan period(s)")
        
        # Get invoice items (prorations)
        invoice_items = self.get_invoice_items_for_customer(customer_db_id)
        print(f"💰 Found {len(invoice_items)} invoice item(s) (including prorations)")
        
        return {
            "customer": customer,
            "subscriptions": detailed_subscriptions,
            "invoices": detailed_invoices,
            "invoice_items": invoice_items,
            "stripe_data": stripe_data,
            "plan_changes": plan_changes,
            "historical_plans": historical_plans,
            "subscription_events": events,
            "summary": self.generate_summary(customer, detailed_subscriptions, detailed_invoices, plan_changes, historical_plans, invoice_items)
        }
    
    def get_subscription_change_events(self, customer_db_id: int) -> List[Dict]:
        """Get subscription-related events that show plan changes"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT 
                    se.stripe_event_id,
                    se.event_type,
                    se.created_at,
                    se.raw_data
                FROM stripe_events se
                WHERE se.event_type IN (
                    'customer.subscription.created',
                    'customer.subscription.updated', 
                    'customer.subscription.deleted',
                    'customer.subscription.trial_will_end',
                    'invoice.payment_succeeded'
                )
                AND se.raw_data::jsonb->'data'->'object'->>'customer' = (
                    SELECT stripe_id FROM customers WHERE id = %s
                )
                ORDER BY se.created_at DESC
            """, (customer_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_plan_changes_from_events(self, events: List[Dict]) -> List[Dict]:
        """Extract plan changes from Stripe events"""
        plan_changes = []
        
        for event in events:
            try:
                event_data = event['raw_data']
                event_type = event['event_type']
                created_at = event['created_at']
                
                if event_type == 'customer.subscription.updated':
                    # Look for plan changes in subscription updates
                    subscription_data = event_data.get('data', {}).get('object', {})
                    items = subscription_data.get('items', {}).get('data', [])
                    
                    for item in items:
                        price = item.get('price', {})
                        product = price.get('product', {})
                        
                        plan_changes.append({
                            'event_type': 'plan_updated',
                            'timestamp': created_at,
                            'subscription_id': subscription_data.get('id'),
                            'product_name': product.get('name') if isinstance(product, dict) else None,
                            'price_amount': price.get('unit_amount'),
                            'currency': price.get('currency'),
                            'interval': price.get('recurring', {}).get('interval'),
                            'lookup_key': price.get('lookup_key'),
                            'event_id': event['stripe_event_id']
                        })
                
                elif event_type == 'customer.subscription.created':
                    # Initial subscription creation
                    subscription_data = event_data.get('data', {}).get('object', {})
                    items = subscription_data.get('items', {}).get('data', [])
                    
                    for item in items:
                        price = item.get('price', {})
                        product = price.get('product', {})
                        
                        plan_changes.append({
                            'event_type': 'plan_created',
                            'timestamp': created_at,
                            'subscription_id': subscription_data.get('id'),
                            'product_name': product.get('name') if isinstance(product, dict) else None,
                            'price_amount': price.get('unit_amount'),
                            'currency': price.get('currency'),
                            'interval': price.get('recurring', {}).get('interval'),
                            'lookup_key': price.get('lookup_key'),
                            'event_id': event['stripe_event_id']
                        })
                
                elif event_type == 'invoice.payment_succeeded':
                    # Look at invoice line items for historical plan info
                    invoice_data = event_data.get('data', {}).get('object', {})
                    lines = invoice_data.get('lines', {}).get('data', [])
                    
                    for line in lines:
                        price = line.get('price', {})
                        product = price.get('product', {}) if price else {}
                        
                        if price and line.get('type') == 'subscription':
                            plan_changes.append({
                                'event_type': 'plan_billed',
                                'timestamp': created_at,
                                'subscription_id': line.get('subscription'),
                                'product_name': product.get('name') if isinstance(product, dict) else None,
                                'price_amount': price.get('unit_amount'),
                                'currency': line.get('currency'),
                                'interval': price.get('recurring', {}).get('interval'),
                                'lookup_key': price.get('lookup_key'),
                                'amount_paid': line.get('amount'),
                                'period_start': datetime.fromtimestamp(line.get('period', {}).get('start', 0)) if line.get('period', {}).get('start') else None,
                                'period_end': datetime.fromtimestamp(line.get('period', {}).get('end', 0)) if line.get('period', {}).get('end') else None,
                                'event_id': event['stripe_event_id']
                            })
                            
            except Exception as e:
                print(f"⚠️  Warning: Could not parse event {event.get('stripe_event_id', 'unknown')}: {e}")
                continue
        
        return plan_changes
    
    def get_historical_plans_from_invoices(self, customer_db_id: int) -> List[Dict]:
        """Get historical plan information from invoice line items"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT DISTINCT
                    ili.period_start,
                    ili.period_end,
                    ili.amount,
                    ili.currency,
                    ili.description,
                    p.stripe_id as price_stripe_id,
                    p.unit_amount,
                    p.recurring_interval,
                    p.lookup_key,
                    p.nickname as price_nickname,
                    pr.stripe_id as product_stripe_id,
                    pr.name as product_name,
                    pr.description as product_description,
                    i.created_at_stripe as invoice_date,
                    i.status as invoice_status,
                    s.stripe_id as subscription_stripe_id
                FROM invoice_line_items ili
                JOIN invoices i ON ili.invoice_id = i.id
                LEFT JOIN subscriptions s ON ili.subscription_id = s.id
                LEFT JOIN prices p ON ili.price_id = p.id
                LEFT JOIN products pr ON p.product_id = pr.id
                WHERE i.customer_id = %s
                AND ili.type = 'subscription'
                ORDER BY ili.period_start DESC
            """, (customer_db_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def generate_summary(self, customer: Dict, subscriptions: List[Dict], invoices: List[Dict], plan_changes: List[Dict] = None, historical_plans: List[Dict] = None, invoice_items: List[Dict] = None) -> Dict:
        """Generate a summary of the customer's plan history"""
        active_subscriptions = [s for s in subscriptions if s['status'] in ['active', 'trialing']]
        
        # Current plans
        current_plans = []
        for sub in active_subscriptions:
            for plan in sub.get('plans', []):
                current_plans.append({
                    'product_name': plan['product_name'],
                    'price': self.format_currency(plan['unit_amount'], plan['currency']),
                    'billing_frequency': self.format_billing_frequency(
                        plan['recurring_interval'], 
                        plan['recurring_interval_count']
                    ),
                    'subscription_status': sub['status'],
                    'current_period_end': sub['current_period_end']
                })
        
        # Historical plans (from all subscriptions)
        all_plans = set()
        for sub in subscriptions:
            for plan in sub.get('plans', []):
                if plan['product_name']:
                    all_plans.add(plan['product_name'])
        
        # Add plans from plan changes history
        if plan_changes:
            for change in plan_changes:
                if change.get('product_name'):
                    all_plans.add(change['product_name'])
        
        # Add plans from historical invoice data
        if historical_plans:
            for plan in historical_plans:
                if plan.get('product_name'):
                    all_plans.add(plan['product_name'])
        
        # Plan change timeline
        plan_timeline = []
        if plan_changes:
            sorted_changes = sorted(plan_changes, key=lambda x: x['timestamp'])
            for change in sorted_changes:
                plan_timeline.append({
                    'date': change['timestamp'],
                    'event': change['event_type'],
                    'plan_name': change.get('product_name'),
                    'price': self.format_currency(change.get('price_amount'), change.get('currency', 'usd')) if change.get('price_amount') else 'N/A',
                    'billing_frequency': f"Every {change.get('interval', 'unknown')}" if change.get('interval') else 'N/A'
                })
        
        # Total spent (including invoice items)
        total_paid = sum(invoice['amount_paid'] or 0 for invoice in invoices)
        if invoice_items:
            # Add positive invoice items (charges), subtract negative ones (credits are already negative)
            total_from_items = sum(item['amount'] or 0 for item in invoice_items)
            total_paid += total_from_items
        
        currency = invoices[0]['currency'] if invoices else 'usd'
        
        # Proration details
        prorations = []
        if invoice_items:
            prorations = [
                {
                    'date': item['created_at'],
                    'description': item['description'],
                    'amount': self.format_currency(item['amount'], item['currency']),
                    'is_proration': item['proration']
                }
                for item in sorted(invoice_items, key=lambda x: x['created_at'])
            ]
        
        return {
            'customer_name': customer['name'],
            'customer_email': customer['email'],
            'total_subscriptions': len(subscriptions),
            'active_subscriptions': len(active_subscriptions),
            'current_plans': current_plans,
            'all_plans_ever_subscribed': list(all_plans),
            'plan_change_timeline': plan_timeline,
            'total_plan_changes': len(plan_changes) if plan_changes else 0,
            'total_invoices': len(invoices),
            'total_invoice_items': len(invoice_items) if invoice_items else 0,
            'proration_history': prorations,
            'total_amount_paid': self.format_currency(total_paid, currency),
            'customer_since': customer['created_at']
        }
    
    def print_formatted_results(self, results: Dict):
        """Print results in a formatted way"""
        if 'error' in results:
            print(f"❌ {results['error']}")
            return
        
        customer = results['customer']
        summary = results['summary']
        
        print("\n" + "="*80)
        print(f"📊 PLAN HISTORY FOR {customer['name'].upper()}")
        print("="*80)
        
        print(f"\n👤 Customer Information:")
        print(f"   Name: {customer['name']}")
        print(f"   Email: {customer['email']}")
        print(f"   Stripe ID: {customer['stripe_id']}")
        print(f"   Customer Since: {customer['created_at']}")
        
        print(f"\n📈 Summary:")
        print(f"   Total Subscriptions: {summary['total_subscriptions']}")
        print(f"   Active Subscriptions: {summary['active_subscriptions']}")
        print(f"   Total Plan Changes: {summary['total_plan_changes']}")
        print(f"   Total Invoices: {summary['total_invoices']}")
        print(f"   Total Invoice Items: {summary.get('total_invoice_items', 0)}")
        print(f"   Total Amount Paid: {summary['total_amount_paid']}")
        
        print(f"\n💰 Proration History (Plan Change Adjustments):")
        if summary.get('proration_history'):
            for i, proration in enumerate(summary['proration_history'], 1):
                print(f"   {i}. {proration['date']} - {proration['amount']}")
                print(f"      {proration['description']}")
                print()
        else:
            print("   No prorations found")
        
        print(f"\n🎯 Current Active Plans:")
        if summary['current_plans']:
            for i, plan in enumerate(summary['current_plans'], 1):
                print(f"   {i}. {plan['product_name']}")
                print(f"      Price: {plan['price']}")
                print(f"      Billing: {plan['billing_frequency']}")
                print(f"      Status: {plan['subscription_status']}")
                print(f"      Next billing: {plan['current_period_end']}")
                print()
        else:
            print("   No active plans")
        
        print(f"\n� Plan Change Timeline:")
        if summary.get('plan_change_timeline'):
            for i, change in enumerate(summary['plan_change_timeline'], 1):
                print(f"   {i}. {change['date']} - {change['event'].replace('_', ' ').title()}")
                print(f"      Plan: {change['plan_name'] or 'Unknown'}")
                print(f"      Price: {change['price']}")
                print(f"      Billing: {change['billing_frequency']}")
                print()
        else:
            print("   No plan changes recorded")
        
        print(f"\n�📜 All Plans Ever Subscribed:")
        if summary['all_plans_ever_subscribed']:
            for i, plan in enumerate(summary['all_plans_ever_subscribed'], 1):
                print(f"   {i}. {plan}")
        else:
            print("   No plans found")
            
        # Show historical plans from invoices if available
        if results.get('historical_plans'):
            print(f"\n📋 Historical Billing Periods:")
            for i, plan in enumerate(results['historical_plans'], 1):
                print(f"   {i}. {plan['product_name'] or 'Unknown Plan'}")
                print(f"      Period: {plan['period_start']} to {plan['period_end']}")
                print(f"      Amount: {self.format_currency(plan['amount'], plan['currency'])}")
                print(f"      Invoice Date: {plan['invoice_date']}")
                print()
        
        print(f"\n🔍 Detailed Subscription History:")
        for i, sub in enumerate(results['subscriptions'], 1):
            print(f"\n   Subscription {i}:")
            print(f"   ├─ Stripe ID: {sub['stripe_id']}")
            print(f"   ├─ Status: {sub['status']}")
            print(f"   ├─ Created: {sub['created_at_stripe']}")
            print(f"   ├─ Period: {sub['current_period_start']} to {sub['current_period_end']}")
            if sub['canceled_at']:
                print(f"   ├─ Canceled: {sub['canceled_at']}")
                if sub['cancellation_reason']:
                    print(f"   ├─ Reason: {sub['cancellation_reason']}")
            
            print(f"   └─ Plans:")
            for j, plan in enumerate(sub.get('plans', []), 1):
                print(f"      {j}. {plan['product_name'] or 'Unknown Product'}")
                print(f"         Price: {self.format_currency(plan['unit_amount'], plan['currency'])}")
                print(f"         Billing: {self.format_billing_frequency(plan['recurring_interval'], plan['recurring_interval_count'])}")
                if plan['quantity'] and plan['quantity'] > 1:
                    print(f"         Quantity: {plan['quantity']}")
        
        print("\n" + "="*80)
    
    def save_to_json(self, results: Dict, filename: str):
        """Save results to JSON file"""
        # Convert datetime objects to strings for JSON serialization
        def json_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, default=json_serializer)
        print(f"📁 Results saved to {filename}")
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()

def main():
    parser = argparse.ArgumentParser(description='Fetch comprehensive plan history for a Stripe customer')
    parser.add_argument('customer_identifier', nargs='?', help='Stripe customer ID or email address')
    parser.add_argument('--email', '-e', action='store_true', help='Treat identifier as email address')
    parser.add_argument('--output', '-o', help='Save results to JSON file')
    parser.add_argument('--json-only', action='store_true', help='Output only JSON (no formatted text)')
    
    args = parser.parse_args()
    
    if not args.customer_identifier:
        parser.print_help()
        print("\nExamples:")
        print("  python get_user_plan_history.py cus_1234567890")
        print("  python get_user_plan_history.py --email user@example.com")
        print("  python get_user_plan_history.py cus_1234567890 --output plan_history.json")
        return
    
    fetcher = UserPlanHistoryFetcher()
    
    try:
        results = fetcher.get_comprehensive_plan_history(
            args.customer_identifier, 
            is_email=args.email
        )
        
        if args.json_only:
            # Convert datetime objects for JSON output
            def json_serializer(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
            
            print(json.dumps(results, indent=2, default=json_serializer))
        else:
            fetcher.print_formatted_results(results)
        
        if args.output:
            fetcher.save_to_json(results, args.output)
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        fetcher.close()

if __name__ == "__main__":
    main()
