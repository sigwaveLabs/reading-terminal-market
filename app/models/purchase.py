from flask import request
from .. import db
from ..models import User
from datetime import datetime
import pytz
from sqlalchemy import CheckConstraint
from flask.ext.login import current_user
from collections import defaultdict, OrderedDict
from ..email import send_email


class CartItem(db.Model):
    """Functions as association table between listings and merchants"""
    __tablename__ = "cartItems"
    __table_args__ = (
        CheckConstraint('quantity > 0'),
    )
    merchant_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'),
                            primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id', ondelete='CASCADE'),
                           primary_key=True)
    quantity = db.Column(db.Integer)

    listing = db.relationship("Listing")

    @staticmethod
    def get_vendor_cart_items_dict():
        """returns a dict where the keys are
        vendors and the fields are a list
        of items in the cart from that vendor"""
        vendor_items_dict = defaultdict(list)
        for item in current_user.cart_items:
            vendor = item.listing.vendor
            vendor_items_dict[vendor].append(item)

        # uses sorted dict so vendor order in cart
        # is in alphabetical order of company names
        sorted_dict = OrderedDict(sorted(vendor_items_dict.items(),
                                         key=lambda pair: pair[0].company_name))
        return sorted_dict

    @staticmethod
    def get_vendor_ids():
        """returns all the ids of all the vendors represented in a cart"""
        return [item.listing.vendor_id for item in current_user.cart_items]

    @staticmethod
    def get_total_price(cart_items=None):
        """returns the total price of the given cart_items. if None, returns
        the total price of all of the current user's cart_items"""
        if cart_items is None:
            cart_items = current_user.cart_items
        prices = [item.listing.price * item.quantity for item in cart_items]
        return sum(prices)

    @staticmethod
    def delete_cart_items(cart_items):
        for cart_item in cart_items:
            db.session.delete(cart_item)

    def __repr__(self):
        return "<CartItem: merchant_id {}, " \
               "listing_id {}, quantity {}>".format(self.merchant_id,
                                                    self.listing_id,
                                                    self.quantity)


class Status:
    PENDING = 0
    APPROVED = 1
    DECLINED = 2


class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)

    date = db.Column(db.DateTime)
    status = db.Column(db.Integer)
    merchant_id = db.Column(db.Integer, db.ForeignKey('merchant.id', ondelete='CASCADE'),
                            primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id', ondelete='CASCADE'),
                           primary_key=True)
    company_name = db.Column(db.String(64))
    referral_name = db.Column(db.String(64))
    comment = db.Column(db.Text)

    def __init__(self, date, vendor_id, referral_name):
        self.status = Status.PENDING
        self.date = date
        self.vendor_id = vendor_id
        self.merchant_id = current_user.id
        vendor = User.query.get(vendor_id)
        self.company_name = vendor.company_name
        self.comment = None
        self.referral_name=referral_name

    def __repr__(self):
        return "<Order: {}>".format(self.id)

    @staticmethod
    def order_cart_items_from_vendor(vendor_id, referral_name, date=None):
        """Orders all the items in the cart from a given vendor"""

        if date is None:
            date = datetime.now(pytz.timezone('US/Eastern'))

        cart_items = filter(lambda item: item.listing.vendor_id == vendor_id,
                            current_user.cart_items)
        order = Order(date, vendor_id, referral_name)
        total = reduce(lambda x,y: x + y, map(lambda item: item.listing.price * item.quantity, cart_items))
        referral_name = referral_name
        vendor = User.query.get(vendor_id)
        merchant_id = current_user.id
        merchant = User.query.get(merchant_id)
        send_email(vendor.email,
                   'New merchant order request',
                   'merchant/email/order_item',
                   merchant=merchant,
                   cart_items=cart_items,
                   referral_name=referral_name,
                   total=total)
        # send confirmation to the merchant
        send_email(merchant.email,
                   'Confirmation of order request',
                   'merchant/email/confirm_order',
                   vendor=vendor,
                   cart_items=cart_items,
                   total=total)
        for item in cart_items:
            p = Purchase(
                order=order,
                listing_id=item.listing.id,
                product_id=item.listing.product_id,
                quantity=item.quantity,
                item_name=item.listing.name,
                item_price=item.listing.price,
                unit=item.listing.unit,
                item_quantity=item.listing.quantity
            )
            db.session.add(p)

        db.session.add(order)
        CartItem.delete_cart_items(cart_items)
        db.session.commit()

    def get_total(self):
        total = 0
        for purchase in self.purchases:
            total += purchase.quantity * purchase.item_price
        return "${0:.2f}".format(total)

    def get_date(self):
        """Get date formatted as mm-dd-yyyy"""
        date = self.date.date()
        return '{}-{}-{}'.format(date.month, date.day, date.year)

    def get_time(self):
        """Get time formatted as mm-dd-yyyy"""
        return self.date.time().strftime("%I:%M%p")

    def get_merchant_info(self):
        merchant_id = self.merchant_id
        merchant = User.query.get(merchant_id)
        if merchant:
            merchant_info = {
                'company_name': merchant.company_name,
                'full_name': merchant.full_name(),
                'email': merchant.email
            }
        else:
            merchant_info = {'company_name': 'USER DELETED FROM SYSTEM' }
        return merchant_info

    def get_vendor_info(self):
        vendor_id = self.vendor_id
        vendor = User.query.get(vendor_id)
        if vendor:
            vendor_info = {
                'company_name': vendor.company_name,
                'full_name': vendor.full_name(),
                'email': vendor.email
            }
        else:
            vendor_info = {'company_name': self.company_name}
        return vendor_info

    def get_purchase_info(self):
        purchases = self.purchases
        purchase_info = []
        for purchase in purchases:
            purchase_info.append({
                'product_id': purchase.product_id, 
                'quantity': purchase.quantity,
                'name': purchase.item_name,
                'price': purchase.item_price,
                'unit': purchase.unit,
                'item_quantity': purchase.item_quantity
            })
        return purchase_info

    @staticmethod
    def order_cart_items():
        """Takes the cart items and makes an order
        for each vendor represented in the cart"""

        date = datetime.now(pytz.timezone('US/Eastern'))
        vendor_ids = set([item.listing.vendor_id for item in current_user.cart_items])
        referral_name=set([item.listing.referral_name for item in current_user.cart_items])
        for vendor_id in vendor_ids:
            Order.order_cart_items_from_vendor(vendor_id, date, referral_name)

    def get_all_purchases(self):
        return Purchase.query.filter_by(order_id=self.id).all()


class Purchase(db.Model):
    __tablename__ = 'purchases'
    id = db.Column(db.Integer, primary_key=True)

    # model relationships
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'))
    order = db.relationship("Order", backref="purchases")
    listing_id = db.Column(db.String(64))
    product_id = db.Column(db.String(64))
    # purchase properties
    quantity = db.Column(db.Integer)
    item_name = db.Column(db.String(64))
    item_price = db.Column(db.Float)
    unit = db.Column(db.String(32))
    item_quantity = db.Column(db.String(32))

    def __init__(self, order, listing_id, product_id, quantity, item_name, item_price, unit, item_quantity):
        self.order = order
        self.listing_id = listing_id
        self.product_id = product_id
        self.quantity = quantity
        self.item_name = item_name
        self.item_price = item_price
        self.unit = unit
        self.item_quantity = item_quantity

    def __repr__(self):
        return "<Purchase: {} Listing: {}>".format(self.id, self.listing_id)
