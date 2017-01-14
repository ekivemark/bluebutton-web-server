import json
import logging
import requests

try:
    # python2
    from urllib import urlencode
except ImportError:
    # python3
    from urllib.parse import urlencode

from collections import OrderedDict

from django.conf import settings
from django.contrib import messages
# from django.core.urlresolvers import reverse_lazy
from django.http import HttpResponseRedirect

from apps.fhir.fhir_core.utils import (kickout_403,
                                       kickout_404)
from apps.fhir.server.models import (SupportedResourceType,
                                     ResourceRouter)
from apps.fhir.bluebutton.models import (BlueButtonText)
from apps.fhir.fhir_core.utils import (error_status,
                                       ERROR_CODE_LIST)

from .models import Crosswalk

PRETTY_JSON_INDENT = 4

FORMAT_OPTIONS_CHOICES = ['json', 'xml']

DF_EXTRA_INFO = False

logger = logging.getLogger('hhs_server.%s' % __name__)


def request_call(request, call_url, fail_redirect="/"):
    """  call to request or redirect on fail"""
    try:
        r = requests.get(call_url)

    except requests.ConnectionError:
        # logger.debug('Problem connecting to FHIR Server')
        messages.error(request, 'FHIR Server is unreachable.')
        return HttpResponseRedirect(fail_redirect)

    if r.status_code in ERROR_CODE_LIST:
        # logger.debug("\nError Status Code:%s" % r.status_code)
        return error_status(r, r.status_code)

    return r


def notNone(value=None, default=None):
    """
    Test value. Return Default if None
    http://stackoverflow.com/questions/4978738/
    is-there-a-python-equivalent-of-the-c-sharp-null-coalescing-operator
    """
    if value is None:
        return default
    else:
        return value


def strip_oauth(get={}):
    """ Remove OAuth values from URL Parameters being sent to backend """

    # access_token can be passed in as a part of OAuth protected request.
    # as can: state=random_state_string&response_type=code&client_id=ABCDEF
    # Remove them before passing url through to FHIR Server

    if get == {}:
        return get

    strip_parms = ['access_token', 'state', 'response_type', 'client_id']

    # logger.debug('Removing:%s from: %s' % (strip_parms, get))

    strip_oauth = get_url_query_string(get, strip_parms)

    # logger.debug('resulting url parameters:%s' % strip_oauth)

    return strip_oauth


def block_params(get, srtc):
    """ strip parameters from search string - get is a dict """

    # Get parameters
    # split on &
    # get srtc.search_block as list
    if get:
        # set search_params to what is received as a default
        search_params = get
    else:
        # No get parameters to process so return
        search_params = ''
        return search_params

    # Now we need to see if there are any get parameters to remove
    if srtc:
        if srtc.override_search:
            search_params = get_url_query_string(get, srtc.get_search_block())

    # do we need to convert result to json. source could be
    # OrderedDict or string
    # search_params_result = json.dumps(search_params)

    # return search_params_result
    return search_params


def add_params(srtc, key=None):
    """ Add filtering parameters to search string """

    # srtc.get_search_add will return a list
    # this will be in form 'Patient={Value}'
    # Replaceable parameters can be included
    # Currently Supported Replaceable Parameters are:
    # %PATIENT% = key
    # key = FHIR_ID for search parameter. eg. patient= Patient profile Id
    # modify this function to add more Replaceable Parameters
    # Need to suppress addition of patient={id} in Patient resource read

    # Returns List

    # add_params = ''
    add_params = []

    if srtc:
        if srtc.override_search:
            params_list = srtc.get_search_add()
            if isinstance(params_list, list):
                pass
            else:
                params_list = [params_list, ]

            # logger.debug('Parameters to add:%s' % params_list)
            # logger.debug('key to replace: %s' % key)

            add_params = []
            for item in params_list:
                # Run through list and do variable replacement
                if srtc.resource_name.lower() not in item:
                    # only replace 'patient=%PATIENT%' if resource not Patient
                    if '%PATIENT%' in item:
                        if key is None:
                            key_str = ''
                        else:
                            # force key to string
                            key_str = str(key)
                        item = item.replace('%PATIENT%', key_str)
                        if '%PATIENT%' in item:
                            # Still there we need to remove
                            item = item.replace('%PATIENT%', '')

                    add_params.append(item)

            # logger.debug('Resulting additional parameters:%s' % add_params)

    return add_params


def concat_parms(front_part={}, back_part={}):
    """ Concatenate the Query Parameters Strings
        The strings should be urlencoded.

    """

    joined_parms = OrderedDict()

    # logger.debug('Joining %s with: %s' % (front_part, back_part))
    if len(front_part) > 0:
        if isinstance(front_part, dict):
            for k, v in front_part.items():
                # append back items
                joined_parms[k] = v
        elif isinstance(front_part, list):
            for item in front_part:
                # split item  on '=' eg. patient=4995802
                item_split = item.split('=')
                if len(item_split) > 1:
                    joined_parms[item_split[0]] = item_split[1]
                else:
                    joined_parms[item_split[0]] = ''

    if len(back_part) > 0:
        if isinstance(back_part, dict):
            for k, v in back_part.items():
                # append back items
                joined_parms[k] = v
        elif isinstance(back_part, list):
            for item in back_part:
                # split item  on '=' eg. patient=4995802
                item_split = item.split('=')
                if len(item_split) > 1:
                    joined_parms[item_split[0]] = item_split[1]
                else:
                    joined_parms[item_split[0]] = ''

    concat_parm = '?' + urlencode(joined_parms)
    # logger.debug("Concat_parm:%s" % concat_parm)
    if concat_parm.startswith('?='):
        concat_parms = '?' + concat_parm[3:]
    else:
        concat_parms = concat_parm
    # logger.debug('resulting string:%s' % concat_parms)

    # We have to do something
    # joined_parms = '?'
    #
    # if len(front_part) != 0:
    #     joined_parms += front_part
    #
    # if len(back_part) == 0:
    #     # nothing to add
    #     return joined_parms
    # else:
    #     joined_parms += '&' + back_part

    return concat_parms


def build_params(get, srtc, key):
    """
    Build the URL Parameters.
    We have to skip any in the skip list.

    :param get:
    :return:
    """
    # We will default to json for content handling
    # FIXME: variables not used
    # in_fmt = 'json'
    # pass_to = ''

    # First we strip the parameters that need to be blocked
    url_param = block_params(get, srtc)

    # Now we need to construct the parameters we need to add
    add_param = add_params(srtc, key)

    # Put the parameters together in urlencoded string
    # leading ? and parameters joined by &
    all_param = concat_parms(url_param, add_param)

    # logger.debug('Parameter (post block/add):%s' % all_param)

    # now we check for _format being specified. Otherwise we get back html
    # by default we will process json unless _format is already set.

    all_param = add_format(all_param)

    # logger.debug('add_Format returned:%s' % all_param)

    return all_param


def add_format(all_param=''):
    """ Check for _format in parameters and add if missing """

    if '_format' in all_param:
        # We have a _format setting.
        # Let's check for xml or json.
        if '_format=json' in all_param.lower():
            return all_param
        elif '_format=xml' in all_param.lower():
            return all_param

    # no _format set.
    # Let's set _format=json.
    if all_param != '':
        all_param += '&'
    else:
        all_param = '?'

    all_param += '_format=json'

    return all_param


def get_url_query_string(get, skip_parm=[]):
    """
    Receive the request.GET Query Dict
    Evaluate against skip_parm by skipping any entries in skip_parm
    Return a query string ready to pass to a REST API.
    http://hl7-fhir.github.io/search.html#all

    # We need to force the key to lower case and skip params should be
    # lower case too

    eg. _lastUpdated=>2010-10-01&_tag=http://acme.org/codes|needs-review

    :param get: {}
    :param skip_parm: []
    :return: Query_String (QS)
    """
    # logger.debug('Evaluating: %s to remove:%s' % (get,skip_parm))

    filtered_dict = OrderedDict()

    # Check we got a get dict
    if not get:
        return filtered_dict
    if not isinstance(get, dict):
        return filtered_dict

    # Now we work through the parameters

    for k, v in get.items():

        # logger.debug('K/V: [%s/%s]' % (k,v))

        if k in skip_parm:
            pass
        else:
            # Build the query_string
            filtered_dict[k] = v

    # qs = urlencode(filtered_dict)
    qs = filtered_dict

    # logger.debug('Filtered parameters:%s from:%s' % (qs, filtered_dict))
    return qs


def bb_update_or_create(user=None, bb_text=None):
    """
    Create a BlueButtonText record if user not found
    else update the record with bb_text
    :param user:
    :param bb_text:
    :return:
    """

    if not bb_text:
        # no text to update
        return None
    result = None
    if user:
        bene, created = BlueButtonText.objects.update_or_create(
            identifier=user, defaults={"bb_content": bb_text}
        )
        if bene.bb_content:
            True
        else:
            False
        result = created
    return result


def check_for_bb_text(user=None):
    """
    Check if there is bb_text
    :param user:
    :return:
    """

    try:
        bb = BlueButtonText.objects.get(user=user)
        return bb
    except BlueButtonText.DoesNotExist:
        return None


def FhirServerUrl(server=None, path=None, release=None):
    # fhir_server_configuration =
    # {'SERVER':'http://fhir-test.bbonfhir.com:8081',
    #                              'PATH':'/',
    #                              'RELEASE':'/baseDstu2'}
    # FHIR_SERVER_CONF = fhir_server_configuration
    # FHIR_SERVER = FHIR_SERVER_CONF['SERVER'] + FHIR_SERVER_CONF['PATH']

    # print("server[%s] or %s" % (server,settings.FHIR_SERVER_CONF['SERVER']))
    # print("path[%s]" % path)
    # print("release[%s]" % release)

    fhir_server = notNone(server, settings.FHIR_SERVER_CONF['SERVER'])

    fhir_path = notNone(path, settings.FHIR_SERVER_CONF['PATH'])

    fhir_release = notNone(release, settings.FHIR_SERVER_CONF['RELEASE'])

    if fhir_release is not None:
        if not fhir_release.endswith('/'):
            fhir_release += '/'

    result = fhir_server
    if result is not None:
        result += fhir_path
    if result is not None:
        result += fhir_release
    # Set to "" if still None
    if result is None:
        result = ""

    return result


def check_access_interaction_and_resource_type(resource_type, intn_type):
    """ usage is deny = check_access_interaction_and_resource_type()

     """
    try:
        rt = SupportedResourceType.objects.get(resource_name=resource_type)
        # force comparison to lower case to make case insensitive check
        if intn_type.lower() not in map(str.lower,
                                        rt.get_supported_interaction_types()):
            msg = 'The interaction: %s is not permitted on %s FHIR ' \
                  'resources on this FHIR sever.' % (intn_type,
                                                     resource_type)
            return kickout_403(msg)
    except SupportedResourceType.DoesNotExist:
        msg = '%s is not a supported resource ' \
              'type on this FHIR server.' % resource_type
        return kickout_404(msg)

    return False


def check_rt_controls(resource_type):
    # Check for controls to apply to this resource_type
    # logger.debug('Resource_Type =%s' % resource_type)
    try:
        srtc = SupportedResourceType.objects.get(resource_name=resource_type)
    except SupportedResourceType.DoesNotExist:
        srtc = None

    return srtc


def masked(srtc=None):
    """ check if force_url_override is set in SupportedResourceType """
    mask = False
    if srtc:
        if srtc.override_url_id:
            mask = True

    return mask


def masked_id(res_type,
              crosswalk=None,
              srtc=None,
              orig_id=None,
              slash=True):
    """ Get the correct id
     if crosswalk.fhir_source.shard_by == resource_type

     """
    id = str(orig_id)
    if srtc:
        if srtc.override_url_id:
            if crosswalk:
                if res_type.lower() == crosswalk.fhir_source.shard_by.lower():
                    # logger.debug('Replacing %s
                    # with %s' % (id, crosswalk.fhir_id))
                    id = crosswalk.fhir_id

    if slash:
        id += '/'

    return id


def mask_with_this_url(request, host_path='', in_text='', find_url=''):
    """ find_url in in_text and replace with url for this server """

    if in_text == '':
        # No text to evaluate
        return in_text

    if find_url == '':
        # no string to find
        return in_text

    # Now we have something to do
    # Get the host name
    # replace_text = request.get_host()
    if host_path.endswith('/'):
        host_path = host_path[:-1]

    out_text = in_text.replace(find_url, host_path)

    # logger.debug('Replacing: [%s] with [%s]' % (find_url, host_path))

    return out_text


def mask_list_with_host(request, host_path, in_text, urls_be_gone=[]):
    """ Replace a series of URLs with the host_name """

    if in_text == '':
        # No text to evaluate
        return in_text

    if len(urls_be_gone) == 0:
        # Nothing in the list to be replaced
        return in_text

    if isinstance(settings.FHIR_SERVER_CONF['REWRITE_FROM'], list):
        for u in settings.FHIR_SERVER_CONF['REWRITE_FROM']:
            if u not in urls_be_gone:
                urls_be_gone.append(u)
    elif isinstance(settings.FHIR_SERVER_CONF['REWRITE_FROM'], str):
        if not settings.FHIR_SERVER_CONF['REWRITE_FROM'] in urls_be_gone:
            urls_be_gone.append(settings.FHIR_SERVER_CONF['REWRITE_FROM'])

    for kill_url in urls_be_gone:
        # work through the list making replacements
        if kill_url.endswith('/'):
            kill_url = kill_url[:-1]

        # print("Replacing:%s" % kill_url)

        in_text = mask_with_this_url(request, host_path, in_text, kill_url)

    return in_text


def get_host_url(request, resource_type=''):
    """ get the full url and split on resource_type """

    if request.is_secure():
        http_mode = 'https://'
    else:
        http_mode = 'http://'

    full_url = http_mode + request.get_host() + request.get_full_path()
    if resource_type == '':
        return full_url
    else:
        full_url_list = full_url.split(resource_type)

    # logger.debug('Full_url as list:%s' % full_url_list)

    return full_url_list[0]


def build_conformance_url():
    """ Build the Conformance URL call string """

    call_to = settings.FHIR_SERVER_CONF['SERVER']
    call_to += settings.FHIR_SERVER_CONF['PATH']
    call_to += settings.FHIR_SERVER_CONF['RELEASE']
    call_to += '/metadata'

    return call_to


def build_output_dict(request,
                      od,
                      resource_type,
                      key,
                      vid,
                      interaction_type,
                      fmt,
                      text_out):
    """ Create the output as an OrderedDict """

    od['resource_type'] = resource_type
    od['id'] = key
    if vid is not None:
        od['vid'] = vid

    # logger.debug('Query List:%s' % request.META['QUERY_STRING'])

    if DF_EXTRA_INFO:
        od['request_method'] = request.method
        od['interaction_type'] = interaction_type
        od['parameters'] = request.GET.urlencode()
        # logger.debug('or:%s' % od['parameters'])
        od['format'] = fmt
        od['note'] = 'This is the %s Pass Thru ' \
                     '(%s) ' % (resource_type, key)

    od['bundle'] = text_out

    return od


def post_process_request(request, fmt, host_path, r_text, rewrite_url_list):
    """ Process request based on xml or json fmt """
    if fmt.lower() == 'xml':
        # We will add xml support later

        text_out = mask_list_with_host(request,
                                       host_path,
                                       r_text,
                                       rewrite_url_list)
        # text_out= minidom.parseString(text_out).toprettyxml()
    else:
        # dealing with json
        # text_out = r.json()
        pre_text = mask_list_with_host(request,
                                       host_path,
                                       r_text,
                                       rewrite_url_list)
        # print("\n\nPRE_TEXT:%s\n\n" % pre_text)
        text_out = json.loads(pre_text, object_pairs_hook=OrderedDict)

    return text_out


def prepend_q(pass_params):
    """ Add ? to parameters if needed """
    if len(pass_params) > 0:
        if pass_params.startswith('?'):
            pass
        else:
            pass_params = '?' + pass_params
    # print("Parameters:", pass_params)
    return pass_params


def pretty_json(od, indent=PRETTY_JSON_INDENT):
    """ Print OrderedDict as pretty indented JSON """

    return json.dumps(od, indent=indent)


def get_default_path(resource_name, crosswalk_source=None):
    """ Get default Path for resource """

    # print("\nGET_DEFAULT_URL:%s" % resource_name)
    if crosswalk_source:
        default_path = crosswalk_source
    else:
        try:
            rr = ResourceRouter.objects.get(supported_resource__resource_name=resource_name)
            default_path = rr.fhir_path
            # print("\nDEFAULT_URL=%s" % default_pathl)

        except ResourceRouter.DoesNotExist:
            # use the default FHIR Server URL
            default_path = FhirServerUrl()
            # print("\nNO MATCH for %s so setting to:%s" % (resource_name,
            #                                               default_path))

    return default_path


def dt_patient_reference(user):
    """ Get Patient Reference from Crosswalk for user """

    if user:
        patient = crosswalk_patient_id(user)
        if patient:
            return {'reference': patient}

    return None


def crosswalk_patient_id(user):
    """ Get patient/id from Crosswalk for user """

    # print("\ncrosswalk_patient_id User:%s" % user)
    try:
        patient = Crosswalk.objects.get(user=user)
        if patient.fhir_id:
            return patient.fhir_id

    except Crosswalk.DoesNotExist:
        pass

    return None
