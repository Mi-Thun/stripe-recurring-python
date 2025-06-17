import os
from flask import Flask, redirect, jsonify, json, request, current_app
from dotenv import load_dotenv
load_dotenv()

import stripe
stripe.api_key = os.getenv('STRIPE_SECRET_KEY') 

app = Flask(__name__,
            static_url_path='',
            static_folder='public')

YOUR_DOMAIN = 'http://localhost:4242'

@app.route('/products', methods=['GET'])
def get_products():
    try:
        product = stripe.Product.list(active=True)
        prices = stripe.Price.list(active=True, expand=["data.product"])
        product_data = []
        for price in prices.data:
            product = price.product
            product_data.append({
                "id": product.id if hasattr(product, "id") else product,
                "name": product.name if hasattr(product, "name") else "",
                "price_id": price.id,
                "lookup_key": price.lookup_key if hasattr(price, "lookup_key") else None, 
                "unit_amount": price.unit_amount,
                "currency": price.currency,
                "interval": price.recurring["interval"] if price.recurring else None,
                "description": product.description if hasattr(product, "description") else "",
            })
        return jsonify(product_data)
    except Exception as e:
        print(e)
        return jsonify({"error": "Failed to fetch products"}), 500


@app.route('/', methods=['GET'])
def get_index():
    return current_app.send_static_file('index.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        prices = stripe.Price.list(
            lookup_keys=[request.form['lookup_key']],
            expand=['data.product']
        )

        checkout_session = stripe.checkout.Session.create(
            line_items=[
                {
                    'price': prices.data[0].id,
                    'quantity': 1,
                },
            ],
            mode='subscription',
            success_url=YOUR_DOMAIN +
            '/success.html?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=YOUR_DOMAIN + '/cancel.html',
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print(e)
        return "Server error", 500

@app.route('/create-portal-session', methods=['POST'])
def customer_portal():
    checkout_session_id = request.form.get('session_id')
    checkout_session = stripe.checkout.Session.retrieve(checkout_session_id)
    return_url = YOUR_DOMAIN

    portalSession = stripe.billing_portal.Session.create(
        customer=checkout_session.customer,
        return_url=return_url,
    )
    return redirect(portalSession.url, code=303)

@app.route('/webhook', methods=['POST'])
def webhook_received():
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    request_data = json.loads(request.data)

    if webhook_secret:
        signature = request.headers.get('stripe-signature')
        try:
            event = stripe.Webhook.construct_event(
                payload=request.data, sig_header=signature, secret=webhook_secret)
            data = event['data']
        except Exception as e:
            return e
        event_type = event['type']
    else:
        data = request_data['data']
        event_type = request_data['type']
    data_object = data['object']
    
    print(data_object)

    print('event ' + event_type)

    if event_type == 'checkout.session.completed':
        print('🔔 Payment succeeded!')
    elif event_type == 'customer.subscription.trial_will_end':
        print('Subscription trial will end')
    elif event_type == 'customer.subscription.created':
        print('Subscription created %s', event.id)
    elif event_type == 'customer.subscription.updated':
        print('Subscription created %s', event.id)
    elif event_type == 'customer.subscription.deleted':
        print('Subscription canceled: %s', event.id)
    elif event_type == 'entitlements.active_entitlement_summary.updated':
        print('Active entitlement summary updated: %s', event.id)

    return jsonify({'status': 'success'})


if __name__ == '__main__':
    app.run(port=4242)