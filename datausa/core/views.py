from flask import Blueprint, request, jsonify
from datausa.attrs.models import Cip, Naics, University
from datausa.core import table_manager
from datausa.core import api, join_api
from datausa.core.models import ApiObject
from datausa.core.crosswalker import crosswalk
from datausa.util.big_places import is_big_geo
from datausa.core.exceptions import DataUSAException


mod = Blueprint('core', __name__, url_prefix='/api')

manager = table_manager.TableManager()

def show_attrs(attr_obj):
    attrs = attr_obj.query.all()
    data = [a.serialize() for a in attrs]
    return jsonify(data=data)

def build_api_obj(api_dict, default_limit=None):
    show = api_dict.get("show", "")
    sumlevel = api_dict.get("sumlevel", "").lower()
    required = api_dict.get("required", "")
    force = api_dict.get("force", "")
    where = api_dict.get("where", "")
    order = api_dict.get("order", "")
    sort = api_dict.get("sort", "")
    limit = api_dict.get("limit", default_limit)
    offset = api_dict.get("offset", None)
    exclude = api_dict.get("exclude", None)
    auto_crosswalk = api_dict.get("auto_crosswalk", False)
    display_names = api_dict.get("display_names", False)

    shows = show.split(",")
    sumlevels = sumlevel.split(",")
    if shows and not sumlevel:
        sumlevels = ["all" for show in shows]
    values = required.split(",") if required else []

    shows_and_levels = {val:sumlevels[idx] for idx, val in enumerate(shows)}

    variables = manager.possible_variables
    vars_and_vals = {var:api_dict.get(var, None) for var in variables}
    vars_and_vals = {k:v for k,v in vars_and_vals.items() if v}


    vars_needed = vars_and_vals.keys() + shows + values
    api_obj = ApiObject(vars_needed=vars_needed, vars_and_vals=vars_and_vals,
                        shows_and_levels=shows_and_levels, values=values,
                        where=where, force=force, order=order,
                        sort=sort, limit=limit, exclude=exclude,
                        auto_crosswalk=auto_crosswalk,
                        display_names=display_names,
                        offset=offset)
    return api_obj

@mod.route("/")
@mod.route("/v1/")
@mod.route("/csv/", defaults={'csv': True})
def api_view(csv=None):
    api_obj = build_api_obj(request.args)
    api_obj = manager.force_1yr_for_big_places(api_obj)
    api_obj = manager.schema_selector(api_obj)
    table_list = manager.all_tables(api_obj)
    table = manager.select_best(table_list, api_obj)
    api_obj.capture_logic(table_list)
    api_obj = manager.crosswalk(table, api_obj)
    data = api.query(table, api_obj, stream=csv)
    return data


def data_call_from_dict(kwargs):
    '''given a dictionary of API call arguments, make the API call'''
    api_obj = build_api_obj(kwargs)
    api_obj = manager.force_1yr_for_big_places(api_obj)
    api_obj = manager.schema_selector(api_obj)
    table_list = manager.all_tables(api_obj)
    table = manager.select_best(table_list, api_obj)
    api_obj.capture_logic(table_list)
    api_obj = manager.crosswalk(table, api_obj)
    cols, data = api.query(table, api_obj, raw=True)
    return cols, data

def search_data_helper(search_headers, search_data, api_args):
    if not api_args:
        return search_headers, search_data

    my_vars = api_args["vars"]
    matched_vars = api_args["matched"]
    if my_vars:
        vars_needed = [rv for v in my_vars for rv in v.related_vars]
        show_kind = my_vars[0].show
        params = {
            "required": ",".join(vars_needed),
            "show": show_kind
        }
        # kwargs = related_vars[0]
        # show_kind = kwargs["show"]
        # kwargs["required"] = ",".join(vars_needed)


        sr_id_idx = search_headers.index("id")
        sr_kind_idx = search_headers.index("kind")
        attr_list = [search_result[sr_id_idx] for search_result in search_data
                    if search_result[sr_kind_idx] == show_kind]

        lookup_values = {}


        if not attr_list and show_kind == 'geo':
            attr_list = ['01000US']

        if attr_list:
            # if show_kind == 'geo':
            #     attr_list.append('01000US')
            params[show_kind] = ",".join(attr_list)
            cols, data = data_call_from_dict(params)

            pos_lookup = {k: cols.index(k) for k in vars_needed}
            id_idx = [str(col).split(".")[-1] for col in cols].index(show_kind)
            for row in data:
                lookup_values[row[id_idx]] = {key: row[pos_lookup[key]] for key in vars_needed}

        # if not lookup_values or "01000US" not in lookup_values or :
        phrase =  "{} in the United States".format(str(matched_vars[0]).title()) if matched_vars else "United States"
        search_data.insert(1, ["01000US", "United States", 15, "geo", phrase, "nation", None, "united-states"])

        search_headers.append('data')
        for search_result in search_data:
            row_id = search_result[0]
            if row_id in lookup_values:
                search_result.append(lookup_values[row_id])


    return search_headers, search_data

@mod.route("/join/")
@mod.route("/join/csv/", defaults={'csv': True})
def api_join_view(csv=None):
    api_obj = build_api_obj(request.args, default_limit=100)
    if api_obj.limit and api_obj.limit > 80000:
        raise DataUSAException("Limit parameter must be less than 80,000")
    tables = manager.required_tables(api_obj)
    data = join_api.joinable_query(tables, api_obj, manager.table_years, csv_format=csv)
    return data


@mod.route("/logic/")
def logic_view():
    api_obj = build_api_obj(request.args)
    table_list = manager.all_tables(api_obj)
    return jsonify(tables=[table.info(api_obj) for table in table_list])

@mod.route("/variables/")
def view_variables():
    '''show available data tables and contained variables'''
    shows = request.args.get("show", "").split(",")
    sumlevels = request.args.get("sumlevel", "").split(",")
    list_all = sumlevels == [""] and shows == [""]
    if sumlevels == [""]:
        sumlevels = ["all"] * len(shows)
    combos = zip(shows, sumlevels)
    results = {table.full_name(): table.col_strs(short_name=True) for table in table_manager.registered_models
               if list_all or all([table.can_show(show, sumlevel) for show,sumlevel in combos])}
    return jsonify(metadata=results)


@mod.route('/table/variables/')
def all_table_vars():
    '''show all available data tables and contained variables'''
    results = {table.full_name(): table.col_strs(short_name=True) for table in table_manager.registered_models}
    return jsonify(metadata=results)

@mod.route("/years/")
def years_view():
    years_data = manager.table_years_set
    return jsonify(data=years_data)
