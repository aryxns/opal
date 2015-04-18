"""
Public facing API views
"""
import collections

from django.conf import settings
from django.views.generic import View
from rest_framework import routers, status, viewsets
from rest_framework.decorators import list_route
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.reverse import reverse

from opal import application, exceptions, glossolalia
from opal.utils import stringport, camelcase_to_underscore, schemas
from opal.utils.models import subrecords
from opal.utils.views import _get_request_data, _build_json_response

app = application.get_app()

# TODO This is stupid - we can fully deprecate this please?
try:
    options = stringport(settings.OPAL_OPTIONS_MODULE)
    micro_test_defaults = options.micro_test_defaults
except AttributeError:
    class options:
        model_names = []
    micro_test_defaults = []


class OPALRouter(routers.DefaultRouter):
    def get_default_base_name(self, viewset):
        name = getattr(viewset, 'base_name', None)
        if name is None:
            return super(OPALRouter, self).get_default_base_name(viewset)
        return name

router = OPALRouter()

class FlowViewSet(viewsets.ViewSet):
    """
    Return the Flow routes for this application.
    
    For more detail on OPAL Flows, see the documentation 
    """
    base_name = 'flow'

    def list(self, request):
        flows = app.flows()
        return Response(flows)


class RecordViewSet(viewsets.ViewSet):
    """
    Return the serialization of all active record types ready to
    initialize on the client side.
    """
    base_name = 'record'

    def list(self, request):
        return Response(schemas.list_records())

    
class ListSchemaViewSet(viewsets.ViewSet):
    """
    Returns the schema for our active lists
    """
    base_name = 'list-schema'

    def list(self, request):
        return Response(schemas.list_schemas())

    
class ExtractSchemaViewSet(viewsets.ViewSet):
    """
    Returns the schema to build our extract query builder
    """
    base_name = 'extract-schema'
    
    def list(self, request):
        return Response(schemas.extract_schema())

# TODO: 
# Deprecate this fully
class OptionsViewSet(viewsets.ViewSet):
    """
    Returns various metadata concerning this OPAL instance: 
    Lookuplists, micro test defaults, tag hierarchy, macros.
    """
    base_name = 'options'
    
    def list(self, request):
        from opal.utils.models import LookupList
        from opal.models import Synonym, Team, Macro
        
        data = {}
        for model in LookupList.__subclasses__():
            options = [instance.name for instance in model.objects.all()]
            data[model.__name__.lower()] = options

        for synonym in Synonym.objects.all():
            try:
                co =  synonym.content_object
            except AttributeError:
                continue
            name = type(co).__name__.lower()
            data[name].append(synonym.name)

        for name in data:
            data[name].sort()

        data['micro_test_defaults'] = micro_test_defaults

        tag_hierarchy = collections.defaultdict(list)
        tag_display = {}

        if request.user.is_authenticated():
            teams = Team.for_user(request.user)
            for team in teams:
                if team.parent:
                    continue # Will be filled in at the appropriate point! 
                tag_display[team.name] = team.title

                subteams = [st for st in teams if st.parent == team]
                tag_hierarchy[team.name] = [st.name for st in subteams]
                for sub in subteams: 
                    tag_display[sub.name] = sub.title

        data['tag_hierarchy'] = tag_hierarchy
        data['tag_display'] = tag_display

        data['macros'] = Macro.to_dict()

        return Response(data)

def item_from_pk(fn):
    """
    Decorator that passes an instance or returns a 404 from pk kwarg.
    """
    def get_item(self, request, pk=None):
        try: 
            item = self.model.objects.get(pk=pk)
        except self.model.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return fn(self, request, item)
    return get_item
    
    
class SubrecordViewSet(viewsets.ViewSet):
    """
    This is the base viewset for our subrecords.
    """
    
    def create(self, request):
        from opal.models import Episode, PatientSubrecord

        subrecord = self.model()
        try:
            episode = Episode.objects.get(pk=request.data['episode_id'])
        except Episode.DoesNotExist:
            return Response('Nonexistant episode', status=status.HTTP_400_BAD_REQUEST)
        pre = episode.to_dict(request.user)

        if isinstance(subrecord, PatientSubrecord):
            del request.data['episode_id']
            patient_id = episode.patient.pk
            request.data['patient_id'] = patient_id

        try:
            subrecord.update_from_dict(request.data, request.user)
        except exceptions.APIError:
            return Response('Unexpected field name', status=status.HTTP_400_BAD_REQUEST)
            
        episode = Episode.objects.get(pk=request.data['episode_id'])
        post = episode.to_dict(request.user)
        glossolalia.change(pre, post)
        
        return Response(post, status=status.HTTP_201_CREATED)

    @item_from_pk
    def retrieve(self, request, item):
        return Response(item.to_dict(request.user))

    @item_from_pk
    def update(self, request, item):
        pass

    @item_from_pk
    def destroy(self, request, item):
        pass
    
for subrecord in subrecords():
    sub_name = camelcase_to_underscore(subrecord.__name__)
    class SubViewSet(SubrecordViewSet):
        base_name = sub_name
        model     = subrecord

    router.register(sub_name, SubViewSet)

router.register('flow', FlowViewSet)
router.register('record', RecordViewSet)
router.register('list-schema', ListSchemaViewSet)
router.register('extract-schema', ExtractSchemaViewSet)
router.register('options', OptionsViewSet)


class APIAdmitEpisodeView(View):
    """
    Admit an episode from upstream!
    """
    def post(self, *args, **kwargs):
        data = _get_request_data(self.request)
        print data
        resp = {'ok': 'Got your admission just fine - thanks!'}
        return _build_json_response(resp)


class APIReferPatientView(View):
    """
    Refer a particular episode of care to a new team
    """
    def post(self, *args, **kwargs):
        """
        Expects PATIENT, EPISODE, TARGET
        """
        from opal.models import Episode
        data = _get_request_data(self.request)
        episode = Episode.objects.get(pk=data['episode'])
        current_tags = episode.get_tag_names(None)
        if not data['target'] in current_tags:
            print "Setting", data['target']
            current_tags.append(data['target'])
            episode.set_tag_names(current_tags, None)
        resp = {'ok': 'Got your referral just fine - thanks!'}
        return _build_json_response(resp)
