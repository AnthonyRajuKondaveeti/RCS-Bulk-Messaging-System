"""
Template Repository Implementation

Concrete implementation of TemplateRepository using SQLAlchemy.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime
import logging

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.template import Template, TemplateStatus, TemplateVariable
from apps.core.ports.repository import TemplateRepository
from apps.adapters.db.models import TemplateModel


logger = logging.getLogger(__name__)


class SQLAlchemyTemplateRepository(TemplateRepository):
    """
    SQLAlchemy implementation of Template Repository
    """
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def save(self, template: Template) -> Template:
        """Save template (insert or update)"""
        # Check if exists
        stmt = select(TemplateModel).where(TemplateModel.id == template.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            # Update existing
            await self._update_from_domain(existing, template)
        else:
            # Create new
            model = self._to_model(template)
            self.session.add(model)
        
        await self.session.flush()
        logger.debug(f"Saved template {template.id}")
        return template
    
    async def get_by_id(self, id: UUID) -> Optional[Template]:
        """Get template by ID"""
        stmt = select(TemplateModel).where(TemplateModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return None
        
        return self._to_domain(model)
    
    async def delete(self, id: UUID) -> bool:
        """Delete template"""
        stmt = select(TemplateModel).where(TemplateModel.id == id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        
        if not model:
            return False
        
        await self.session.delete(model)
        await self.session.flush()
        return True
    
    async def exists(self, id: UUID) -> bool:
        """Check if template exists"""
        stmt = select(TemplateModel.id).where(TemplateModel.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
    
    async def get_by_tenant(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[TemplateStatus] = None,
    ) -> List[Template]:
        """Get templates for a tenant"""
        stmt = select(TemplateModel).where(
            TemplateModel.tenant_id == tenant_id
        )
        
        if status:
            stmt = stmt.where(TemplateModel.status == status)
        
        stmt = stmt.order_by(TemplateModel.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        
        return [self._to_domain(model) for model in models]

    def _to_domain(self, model: TemplateModel) -> Template:
        """Convert ORM model to domain entity"""
        # Map variables JSON to TemplateVariable objects
        variables = []
        if model.variables:
            for v in model.variables:
                variables.append(
                    TemplateVariable(
                        name=v["name"],
                        description=v["description"],
                        required=v.get("required", True),
                        default_value=v.get("default_value"),
                        validation_regex=v.get("validation_regex"),
                    )
                )

        template = Template(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            content=model.content,
            status=model.status,
            variables=variables,
            rich_card_template=model.rich_card_template,
            suggestions_template=model.suggestions_template,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
        
        template.description = model.description
        template.category = model.category
        template.tags = model.tags or []
        template.language = model.language
        template.usage_count = model.usage_count
        template.last_used_at = model.last_used_at
        
        return template

    def _to_model(self, template: Template) -> TemplateModel:
        """Convert domain entity to ORM model"""
        # Map TemplateVariable objects to JSON
        variables_json = [
            {
                "name": v.name,
                "description": v.description,
                "required": v.required,
                "default_value": v.default_value,
                "validation_regex": v.validation_regex,
            }
            for v in template.variables
        ]

        return TemplateModel(
            id=template.id,
            tenant_id=template.tenant_id,
            name=template.name,
            content=template.content,
            status=template.status,
            variables=variables_json,
            rich_card_template=template.rich_card_template,
            suggestions_template=template.suggestions_template,
            description=template.description,
            category=template.category,
            tags=template.tags,
            language=template.language,
            usage_count=template.usage_count,
            last_used_at=template.last_used_at,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    async def _update_from_domain(
        self,
        model: TemplateModel,
        template: Template,
    ) -> None:
        """Update ORM model from domain entity"""
        model.name = template.name
        model.content = template.content
        model.status = template.status
        
        # Update variables
        model.variables = [
            {
                "name": v.name,
                "description": v.description,
                "required": v.required,
                "default_value": v.default_value,
                "validation_regex": v.validation_regex,
            }
            for v in template.variables
        ]
        
        model.rich_card_template = template.rich_card_template
        model.suggestions_template = template.suggestions_template
        model.description = template.description
        model.category = template.category
        model.tags = template.tags
        model.language = template.language
        model.usage_count = template.usage_count
        model.last_used_at = template.last_used_at
        model.updated_at = datetime.utcnow()
