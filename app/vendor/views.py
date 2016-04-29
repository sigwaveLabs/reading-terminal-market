from ..decorators import vendor_required
from flask import (
    render_template,
    abort,
    redirect,
    flash,
    url_for,
    request,
    jsonify,
    json
)
from flask.ext.login import login_required, current_user
from forms import (ChangeListingInformation, NewItemForm, NewCSVForm)
from . import vendor
from ..models import Listing, Order, Status, User
from ..models.listing import Updated
from ..models.user import Vendor
import csv
import re
import copy
from .. import db
from ..email import send_email
from pint import UnitRegistry, UndefinedUnitError


@vendor.route('/')
@login_required
@vendor_required
def index():
    tut_completed = User.query.filter_by(id=current_user.id).first().tutorial_completed
    return render_template('vendor/index.html', tut_completed=tut_completed)


@vendor.route('/tutorial_completed', methods=['POST'])
@login_required
@vendor_required
def tutorial_completed():
    current_tutorial_status = User.query.filter_by(id=current_user.id).first().tutorial_completed;
    User.query.filter_by(id=current_user.id).first().tutorial_completed = \
        not User.query.filter_by(id=current_user.id).first().tutorial_completed;
    db.session.commit();
    return '', 204


@vendor.route('/new-item', methods=['GET', 'POST'])
@login_required
@vendor_required
def new_listing():
    tut_completed = User.query.filter_by(id=current_user.id).first().tutorial_completed
    """Create a new item."""
    form = NewItemForm()
    if form.validate_on_submit():
        listing = Listing(
            name=form.listing_name.data,
            description=form.listing_description.data,
            available=True,
            unit= form.listing_unit.data,
            quantity= form.listing_quantity.data,
            price=form.listing_price.data,
            vendor_id=current_user.id,
            product_id=form.listing_productID.data
        )
        db.session.add(listing)
        db.session.commit()
        flash('Item {} successfully created'.format(listing.name),
              'form-success')
        return redirect(url_for('.new_listing', tut_completed=tut_completed))
    return render_template('vendor/new_listing.html', form=form, tut_completed=tut_completed)


@vendor.route('/csv-upload', methods=['GET', 'POST'])
@login_required
@vendor_required
def csv_upload():
    tut_completed = User.query.filter_by(id=current_user.id).first().tutorial_completed
    """Create a new item."""
    form = NewCSVForm()
    listings = []
    current_row = 0
    if form.validate_on_submit():
        if test_csv(form):
            csv_field = form.file_upload
            buff = csv_field.data.stream
            buff.seek(0)
            csv_data = csv.DictReader(buff, delimiter=',')
            #for each row in csv, create a listing
            current_vendor = Vendor.get_vendor_by_user_id(user_id=current_user.id)
            for row in csv_data:
                print 'in here!'
                #cheap way to skip weird 'categorical' lines
                if (row[current_vendor.product_id_col]).strip().isdigit():
                    safe_price = row[current_vendor.price_col]
                    proposed_listing = Listing.add_csv_row_as_listing(csv_row=row, price=safe_price)
                    queried_listing = Listing.get_listing_by_product_id(product_id=row[current_vendor.product_id_col])
                    if queried_listing:
                        # case: listing exists and price has not changed
                        if queried_listing.price == float(safe_price):
                            proposed_listing.updated = Updated.NO_CHANGE
                            listings.append(proposed_listing)
                        # case: listing exists and price has changed
                        else:
                            queried_listing.price = float(safe_price)
                            proposed_listing.price = float(safe_price)
                            proposed_listing.updated = Updated.PRICE_CHANGE
                            listings.append(proposed_listing)
                            db.session.commit()
                        #case: listing does not yet exist
                    else:
                        proposed_listing.updated = Updated.NEW_ITEM
                        listings.append(proposed_listing)
                        Listing.add_listing(new_listing=proposed_listing)
    return render_template('vendor/new_csv.html', tut_completed=tut_completed, form=form, listings=listings)

#get rid of those pesky dollar signs that mess up parsing
def stripPriceHelper(price):
    r = re.compile("\$(\d+.\d+)")
    return r.search(price.replace(',','')).group(1)


def is_numeric_col(current_vendor, row, col, row_count):
    if not row[col].isdigit() and row[col]:
        flash("Error parsing {}'s CSV file. Bad entry in {} column, at row {} "
              .format(current_vendor.full_name(),col, row_count),
              'form-error')
        return False
    return True


def is_proper_unit(vendor_name, unit, row, row_count):
    ureg = UnitRegistry()
    try:
        ureg.parse_expression(row[unit])
    except UndefinedUnitError:
        flash("Error parsing {}'s CSV file. Bad entry in the {} column, at row {} "
              .format(vendor_name, unit, row_count),'form-error')
        return False
    return True

@vendor_required
def test_csv(form):
    current_vendor = Vendor.get_vendor_by_user_id(user_id=current_user.id)
    if current_vendor is None:
        abort(404)
    columns = [current_vendor.product_id_col,current_vendor.listing_description_col, current_vendor.unit_col,
               current_vendor.price_col, current_vendor.name_col, current_vendor.quantity_col]
    csv_file = form.file_upload
    buff = csv_file.data.stream
    csv_data = csv.DictReader(buff, delimiter=',')
    c = current_vendor.product_id_col
    row_count = 0
    for row in csv_data:
        if len(row.keys()) > 1:
            row_count += 1
            for c in columns:
                    if c not in row:
                        flash("Error parsing {}'s CSV file. Couldn't find {} column at row {}"
                              .format(current_vendor.full_name(),c, row_count),
                              'form-error')
                        return False
                    if row[current_vendor.product_id_col]=="" and row[current_vendor.listing_description_col]=="":
                        flash("Successfully parsed {}'s CSV file!"
                        .format(current_vendor.full_name()), 'form-success')
                        return True
            if not(
                is_numeric_col(current_vendor=current_vendor, row=row,
                              col=current_vendor.price_col, row_count=row_count) and
                is_numeric_col(current_vendor=current_vendor, row=row,
                               col=current_vendor.quantity_col,row_count=row_count) and
                is_numeric_col(current_vendor=current_vendor, row=row,
                               col=current_vendor.product_id_col,row_count=row_count)):
                return False
            if not is_proper_unit(current_vendor.full_name(), current_vendor.unit_col,row, row_count):
                return False
    return True


@vendor.route('/itemslist/')
@vendor.route('/itemslist/<int:page>')
@login_required
@vendor_required
def current_listings(page=1):
    """View all current listings."""
    tut_completed = User.query.filter_by(id=current_user.id).first().tutorial_completed
    main_search_term = request.args.get('main-search', "", type=str)
    sort_by = request.args.get('sort-by', "", type=str)
    avail = request.args.get('avail', "", type=str)
    search = request.args.get('search', "", type=str)
    listings_raw = Listing.search(
        sort_by=sort_by,
        main_search_term=main_search_term,
        avail=avail
    )
    listings_raw = listings_raw.filter(Listing.vendor_id == current_user.id)

    if search != "False":
        page = 1

    listings_paginated = listings_raw.paginate(page, 21, False)
    result_count = listings_raw.count()

    if result_count > 0:
        header = "Search Results: {} in total".format(result_count)
    else:
        header = "No Search Results"

    return render_template(
        'vendor/current_listings.html',
        listings=listings_paginated,
        main_search_term=main_search_term,
        sort_by=sort_by,
        count=result_count,
        header=header,
        tut_completed=tut_completed
    )


@vendor.route('/items/<int:listing_id>')
@vendor.route('/items/<int:listing_id>/info')
@login_required
@vendor_required
def listing_info(listing_id):
    """View a listing's info."""
    listing = Listing.query.filter_by(id=listing_id).first()

    if listing is None:
        abort(404)
    elif listing.vendor_id != current_user.id:
        abort(403)

    return render_template('vendor/manage_listing.html', listing=listing)


@vendor.route('/items/<int:listing_id>/edit-item', methods=['GET', 'POST'])
@login_required
@vendor_required
def change_listing_info(listing_id):
    """Change a listings's info."""
    listing = Listing.query.filter_by(
        id=listing_id,
        vendor_id=current_user.id
    ).first()

    if listing is None:
        abort(404)

    form = ChangeListingInformation()
    form.listing_id = listing_id

    if form.validate_on_submit():
        listing.name = form.listing_name.data
        listing.description = form.listing_description.data
        listing.unit = form.listing_unit.data
        listing.quantity = form.listing_quantity.data
        if form.listing_available.data:
            listing.available = True
        else:
            listing.disable_listing()
        listing.price = form.listing_price.data
        listing.vendor_id = current_user.id
        flash('Information for item {} successfully changed.'
              .format(listing.name), 'form-success')

    form.listing_name.default = listing.name
    form.listing_description.default = listing.description
    form.listing_price.default = listing.price
    form.listing_unit.default = listing.unit
    form.listing_quantity.default = listing.quantity
    form.listing_available.default = listing.available

    form.process()

    return render_template(
        'vendor/manage_listing.html',
        listing=listing,
        form=form
    )





@vendor.route('/item/<int:listing_id>/delete')
@login_required
@vendor_required
def delete_listing_request(listing_id):
    """Request deletion of an item"""
    listing = Listing.query.filter_by(
        id=listing_id,
        vendor_id=current_user.id
    ).first()
    if listing is None:
        abort(404)
    return render_template('vendor/manage_listing.html', listing=listing)


@vendor.route('/item/<int:listing_id>/_delete')
@login_required
@vendor_required
def delete_listing(listing_id):
    """Delete an item."""
    listing = Listing.query.filter_by(
        id=listing_id,
        vendor_id=current_user.id
    ).first()
    listing.delete_listing()
    flash('Successfully deleted item %s.' % listing.name, 'success')
    return redirect(url_for('vendor.current_listings'))


@vendor.route('/orders')
@login_required
@vendor_required
def view_orders():
    orders = (Order.query.filter_by(vendor_id=current_user.id)
              .order_by(Order.id.desc()))
    status_filter = request.args.get('status')

    if status_filter == 'approved':
        orders = orders.filter_by(status=Status.APPROVED)
    elif status_filter == 'declined':
        orders = orders.filter_by(status=Status.DECLINED)
    elif status_filter == 'pending':
        orders = orders.filter_by(status=Status.PENDING)
    else:
        status_filter = None

    return render_template(
        'vendor/orders.html',
        orders=orders.all(),
        status_filter=status_filter
    )


@vendor.route('/approve/<int:order_id>', methods=['POST'])
@login_required
@vendor_required
def approve_order(order_id):
    order = Order.query.get(order_id)
    if not order or order.vendor_id != current_user.id:
        abort(404)
    if order.status != Status.PENDING:
        abort(400)
    order.status = Status.APPROVED
    order.comment = request.json['comment']
    db.session.commit()

    merchant_id = order.merchant_id
    merchant = User.query.get(merchant_id)

    vendor_name = order.company_name
    purchases = order.purchases
    comment = order.comment
    send_email(merchant.email,
               'Vendor order request approved',
               'vendor/email/approved_order',
               vendor_name=vendor_name,
               order=order,
               purchases=purchases,
               comment=comment)

    return jsonify({'order_id': order_id, 'status': 'approved', 'comment': comment})


'''@vendor.route('/request-tag', methods=['PUT'])
@login_required
@vendor_required
def request_tag():
    form = RequestTagForm()
    if form.validate():
        for tag in form.tags_chosen:
            User.vendor_tags_table.append(current_user, tag, False)
        for tag in User.tags_list:
            if tag not in form.tags_chosen:
                User.vendor_tags_table.delete(current_user, tag)'''


@vendor.route('/decline/<int:order_id>', methods=['POST'])
@login_required
@vendor_required
def decline_order(order_id):
    order = Order.query.get(order_id)
    if not order or order.vendor_id != current_user.id:
        abort(404)
    if order.status != Status.PENDING:
        abort(400)
    order.status = Status.DECLINED
    order.comment = request.json['comment']
    db.session.commit()

    merchant_id = order.merchant_id
    merchant = User.query.get(merchant_id)
    vendor_name = order.company_name
    vendor_email = current_user.email
    purchases = order.purchases
    comment = order.comment
    send_email(merchant.email,
               'Vendor order request declined',
               'vendor/email/declined_order',
               vendor_name=vendor_name,
               vendor_email=vendor_email,
               order=order,
               purchases=purchases,
               comment=comment)
    return jsonify({'order_id': order_id, 'status': 'declined', 'comment': comment})
