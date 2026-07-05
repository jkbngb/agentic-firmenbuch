#! /usr/bin/env python3.6

"""
server.py
Stripe Sample.
Python 3.6 or newer required.

NOTE (agentic-firmenbuch): This is Stripe's official "Prebuilt subscriptions"
sample, kept for REFERENCE only. It is NOT deployed. The original file had a
hard-coded test secret key on the StripeClient line; it has been replaced with
os.environ["STRIPE_SECRET_KEY"] so that no secret is ever committed. See
PATTERNS.md for which patterns we adopt into our Starlette MCP stack.
"""
import os
from flask import Flask, redirect, jsonify, json, request, current_app

import stripe
# This is your test secret API key.
# Don't put any keys in code. See https://docs.stripe.com/keys-best-practices.
# SCRUBBED: original sample hard-coded a sk_test_ key here; read from ENV instead.
client = stripe.StripeClient(os.environ["STRIPE_SECRET_KEY"])

app = Flask(__name__,
            static_url_path='',
            static_folder='public')

YOUR_DOMAIN = 'http://localhost:4242'

@app.route('/', methods=['GET'])
def get_index():
    return current_app.send_static_file('index.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        prices = client.v1.prices.list(params={
            'lookup_keys': [request.form['lookup_key']],
            'expand': ['data.product'],
        })

        checkout_session = client.v1.checkout.sessions.create(params={
            'line_items': [
                {
                    'price': prices.data[0].id,
                    'quantity': 1,
                },
            ],
            'mode': 'subscription',
            'success_url': YOUR_DOMAIN +
            '/success.html?session_id={CHECKOUT_SESSION_ID}',
        })
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print(e)
        return "Server error", 500

@app.route('/create-portal-session', methods=['POST'])
def customer_portal():
    # For demonstration purposes, we're using the Checkout session to retrieve the customer_account ID.
    # Typically this is stored alongside the authenticated user in your database.
    checkout_session_id = request.form.get('session_id')
    checkout_session = client.v1.checkout.sessions.retrieve(checkout_session_id)

    # This is the URL to which the customer will be redirected after they're
    # done managing their billing with the portal.
    return_url = YOUR_DOMAIN

    portalSession = client.v1.billing_portal.sessions.create(params={
        'customer': checkout_session.customer,
        'return_url': return_url,
    })
    return redirect(portalSession.url, code=303)

@app.route('/webhook', methods=['POST'])
def webhook_received():
    # To run this example, set an environment variable STRIPE_WEBHOOK_SECRET to
    # your endpoint's unique secret.
    #
    # If you are testing with the CLI, find the secret by running 'stripe listen'.
    # If you are using an endpoint defined with the API or dashboard, look in
    # your webhook settings at https://dashboard.stripe.com/webhooks.
    #
    # Don't include webhook secrets in code.
    webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

    request_data = json.loads(request.data)

    if webhook_secret:
        # Retrieve the event by verifying the signature using the raw body and secret if webhook signing is configured.
        signature = request.headers.get('stripe-signature')
        try:
            event = client.construct_event(
                payload=request.data, sig_header=signature, secret=webhook_secret)
            data = event['data']
        except Exception as e:
            return e
        # Get the type of webhook event sent - used to check the status of PaymentIntents.
        event_type = event['type']
    else:
        data = request_data['data']
        event_type = request_data['type']
    data_object = data['object']

    print('event ' + event_type)

    if event_type == 'checkout.session.completed':
        print('Payment succeeded!')
    elif event_type == 'customer.subscription.trial_will_end':
        print('Subscription trial will end')
    elif event_type == 'customer.subscription.created':
        print('Subscription created %s', event.id)
    elif event_type == 'customer.subscription.updated':
        print('Subscription created %s', event.id)
    elif event_type == 'customer.subscription.deleted':
        # handle subscription canceled automatically based
        # upon your subscription settings. Or if the user cancels it.
        print('Subscription canceled: %s', event.id)
    elif event_type == 'entitlements.active_entitlement_summary.updated':
        # handle active entitlement summary updated
        print('Active entitlement summary updated: %s', event.id)

    return jsonify({'status': 'success'})


if __name__ == '__main__':
    app.run(port=4242)
