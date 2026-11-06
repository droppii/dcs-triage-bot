#!/usr/bin/env python3
"""STAGING test data for DCS bot cases (user-authorized 2026-11-06). Only test account nhudoan01 + its customer Dtmt.
  stg_case.py cart                 -> token, add 1 SKU, delivery options, pricing (prints ids/prices only)
  stg_case.py checkout <json>      -> checkout my open cart with the given extra fields
  stg_case.py voucher <code>       -> try to claim a voucher (case 2)
"""
import json, os, sys, urllib.request, urllib.error

API = 'https://apistg.droppii.com'
USER, PASSWORD = os.environ.get('STG_TEST_USER', 'nhudoan01'), os.environ.get('STG_TEST_PASSWORD')  # set in .env, never commit
SKU = 'e3c43b11-8b7f-4bca-aa54-beca14122572'      # Sữa chua sấy khô có tổ yến thật (12 viên)
WAREHOUSE = 'e4633235-4758-4b1c-8785-624046e92361'  # Tp. Hồ Chí Minh (Droppii)
CUSTOMER = 'c4f0719d-9d52-456e-aa1a-f9ecb372fad4'   # Dtmt (existing customer of nhudoan01)
ADDRESS = 'f4a9566a-d9e9-45ba-b9d4-57e896b2c1b5'
PROVIDER = 'c4f55b8a-85cb-459d-be6c-6732f004f492'   # provider of template order VNW07902587828
DROP = {'address', 'phone', 'fullAddress', 'customerPhone', 'customerAddress', 'email', 'customerFullAddress', 'streetAddress'}


def call(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'droppii-qa-dcs-bot-test',
                                          **({'Authorization': f'Bearer {token}'} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'null')
        except ValueError:
            return e.code, None


def scrub(o):
    if isinstance(o, dict):
        return {k: scrub(v) for k, v in o.items() if k not in DROP}
    if isinstance(o, list):
        return [scrub(x) for x in o[:8]]
    return o


def token():
    assert PASSWORD, "set STG_TEST_PASSWORD"
    s, r = call('POST', '/identity-service/v1/identity/get-token', {'userName': USER, 'password': PASSWORD})
    assert s == 200 and r.get('data', {}).get('accessToken'), f'login failed {s}'
    return r['data']['accessToken']


def show(label, s, r, keys=None):
    d = (r or {}).get('data', r) if isinstance(r, dict) else r
    if keys and isinstance(d, dict):
        d = {k: d.get(k) for k in keys}
    print(f'== {label}: HTTP {s} statusCode={(r or {}).get("statusCode") if isinstance(r, dict) else None} '
          f'msg={(r or {}).get("message") if isinstance(r, dict) else None}')
    print(json.dumps(scrub(d), ensure_ascii=False)[:2500])


def main():
    cmd = sys.argv[1]
    t = token()
    if cmd == 'cart':
        s, r = call('POST', '/order-service/v1/app/carts/my-open-cart/items',
                    {'productSkuId': SKU, 'productType': 'NORMAL_PRODUCT', 'inventoryId': WAREHOUSE, 'quantity': 1}, t)
        show('add item', s, r)
    if cmd in ('cart', 'quote'):
        s, r = call('POST', '/order-service/v1/app/carts/my-open-cart/delivery-options',
                    {'addressId': ADDRESS, 'inventoryId': WAREHOUSE}, t)
        show('delivery options', s, r)
        s, r = call('POST', '/order-service/v1/app/carts/my-open-cart/pricing',
                    [{'inventoryId': WAREHOUSE, 'addressId': ADDRESS, 'deliveryProviderId': PROVIDER,
                      'paymentMethodId': 1, 'orderType': 'NORMAL', 'doesCustomerPayFee': True}], t)
        show('pricing', s, r)
    elif cmd == 'checkout':
        body = {'inventoryId': WAREHOUSE, 'addressId': ADDRESS, 'customerId': CUSTOMER, **json.loads(sys.argv[2])}
        s, r = call('POST', '/order-service/v1/app/carts/my-open-cart/checkout', body, t)
        show('checkout', s, r)
    elif cmd == 'voucher':
        s, r = call('POST', '/promotion-service/v2/app/vouchers/claim', {'vouchers': [{'promotionCode': sys.argv[2]}]}, t)
        show('claim voucher', s, r)


if __name__ == '__main__':
    main()
