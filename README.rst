=================================
    django-prefetch-decorator
=================================

Django 1.4 has prefetch_related, and it's grate! But sometimes it's hard to guess what we need to prefetch. And here comes prefetch decorator.


Example
=======

Here's a simplified example of using prefetch decorator:

.. code-block:: python

    from django.conf import settings
    from prefetch import prefetch
    from dateutil import tz

    settings.PREFETCH_ALIASES = {
        'region': 'this_app.Region',
        'business': 'this_app.Business'
    }

    class Timezone(model.Model):
        long_name = models.TextField()


    class Region(models.Model):
        timezone = models.ForeignKey(Timezone, null=True)


        @prefetch(region=['timezone'])
        def getz(self):
            return tz.gettz(self.timezone.long_name)  # add timezone to region's prefetch


    class Business(models.Model):
         regions = models.ManyToManyField('this_app.Region',
                                          related_name='businesses')

        # alternative @prefetch(business=[('regions', ('region', 'gettz'))])
        @prefetch(business=[('regions', ('this_app.Region', 'gettz'))])
        def get_timezone(self):
        try:
            r = self.regions.all()[0]  # small amount of regions (nice to prefetch)
            return r.gettz() # add this sentence to prefetch
        except IndexError:
            return tz.gettz('UTC')
    

    def some_view(request):
        # (...)
        r = Business.objects.prefetch_related(Business.get_timezone.get_prefetch(Business)).get(pk=id)
        r.get_timezone()  # fully prefetch for this func no db calls


    
    >>> Business.get_timezone.get_prefetch
    {<function lib.prefetch.<lambda>>: [<function lib.prefetch.<lambda>>, 'regions']}
    
    # after lazy loadings (when get_prefetch was called) same prefetch:
    {some.path.to.model.Business: ['regions__timezone']}




