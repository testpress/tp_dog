"""Integrations module for tp_dog."""

from tp_dog.integrations.base import BaseIntegration
from tp_dog.integrations.django import DjangoIntegration
from tp_dog.integrations.manager import IntegrationManager, get_integration_manager
from tp_dog.integrations.postgres import PostgresIntegration
from tp_dog.integrations.redis import RedisIntegration
from tp_dog.integrations.requests import RequestsIntegration

__all__ = [
    "BaseIntegration",
    "DjangoIntegration",
    "PostgresIntegration",
    "RedisIntegration",
    "RequestsIntegration",
    "IntegrationManager",
    "get_integration_manager",
]

