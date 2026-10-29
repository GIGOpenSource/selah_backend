#!/usr/bin/env python
# -*- coding: UTF-8 -*-
from rest_framework import routers
from Apps.views.Example.view import ExampleViewSet

router = routers.DefaultRouter()
router.register(r'examples', ExampleViewSet, basename='example')

urlpatterns = router.urls
