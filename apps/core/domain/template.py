"""
Template Domain Model

Represents RCS message templates with variable substitution,
rich content support, and validation.

Templates are value objects that define reusable message formats
with placeholders for dynamic content.

Example:
    >>> template = Template.create(
    ...     name="Order Shipped",
    ...     content="Hi {{customer_name}}, your order {{order_id}} has shipped!",
    ...     variables=["customer_name", "order_id"]
    ... )
    >>> content = template.render({
    ...     "customer_name": "John",
    ...     "order_id": "#1234"
    ... })
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set
from uuid import UUID, uuid4
from datetime import datetime
import re

from apps.core.domain.message import MessageContent, RichCard, SuggestedAction


class TemplateStatus(str):
    """Template approval status"""
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass
class TemplateVariable:
    """Template variable definition"""
    name: str
    description: str
    required: bool = True
    default_value: Optional[str] = None
    validation_regex: Optional[str] = None
    
    def validate(self, value: str) -> bool:
        """Validate variable value"""
        if self.validation_regex:
            return bool(re.match(self.validation_regex, value))
        return True


class Template:
    """
    Message Template Value Object
    
    Represents a reusable message template with variable substitution.
    Templates support both plain text and RCS rich content.
    
    Business Rules:
        1. All variables in content must be declared
        2. Required variables must have values when rendering
        3. Templates must be approved before use in campaigns
        4. Content cannot exceed 1024 characters
        
    Example:
        >>> template = Template.create(
        ...     name="Order Confirmation",
        ...     content="Thanks {{name}}! Order {{order_id}} confirmed.",
        ...     variables=["name", "order_id"],
        ... )
        >>> content = template.render({"name": "Alice", "order_id": "123"})
    """
    
    def __init__(
        self,
        id: UUID,
        tenant_id: UUID,
        name: str,
        content: str,
        status: str = TemplateStatus.DRAFT,
        variables: Optional[List[TemplateVariable]] = None,
        rich_card_template: Optional[Dict[str, Any]] = None,
        suggestions_template: Optional[List[Dict[str, Any]]] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ):
        self.id = id
        self.tenant_id = tenant_id
        self.name = name
        self.content = content
        self.status = status
        self.variables = variables or []
        self.rich_card_template = rich_card_template
        self.suggestions_template = suggestions_template or []
        self.created_at = created_at or datetime.utcnow()
        self.updated_at = updated_at or datetime.utcnow()
        
        # Metadata
        self.description: Optional[str] = None
        self.category: Optional[str] = None
        self.tags: List[str] = []
        self.language: str = "en"
        
        # Usage statistics
        self.usage_count: int = 0
        self.last_used_at: Optional[datetime] = None
        
        # Validation
        self._validate_template()
    
    @classmethod
    def create(
        cls,
        tenant_id: UUID,
        name: str,
        content: str,
        variables: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> "Template":
        """
        Create a new template
        
        Args:
            tenant_id: Tenant identifier
            name: Template name
            content: Message content with {{variable}} placeholders
            variables: List of variable names
            description: Template description
            
        Returns:
            New Template instance
            
        Raises:
            ValueError: If content is invalid
        """
        # Create template variables from names
        template_vars = []
        if variables:
            for var_name in variables:
                template_vars.append(
                    TemplateVariable(
                        name=var_name,
                        description=f"Variable: {var_name}",
                        required=True,
                    )
                )
        
        template = cls(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name.strip(),
            content=content.strip(),
            variables=template_vars,
            status=TemplateStatus.DRAFT,
        )
        
        if description:
            template.description = description
        
        return template
    
    def render(self, variable_values: Dict[str, str]) -> MessageContent:
        """
        Render template with actual values
        
        Args:
            variable_values: Dictionary of variable names to values
            
        Returns:
            MessageContent with substituted values
            
        Raises:
            ValueError: If required variables are missing
            
        Example:
            >>> values = {"customer_name": "John", "order_id": "1234"}
            >>> content = template.render(values)
        """
        # Validate required variables
        self._validate_variables(variable_values)
        
        # Substitute variables in content
        rendered_text = self.content
        for var in self.variables:
            placeholder = f"{{{{{var.name}}}}}"
            value = variable_values.get(var.name, var.default_value or "")
            
            # Validate value
            if not var.validate(value):
                raise ValueError(
                    f"Invalid value for variable '{var.name}': {value}"
                )
            
            rendered_text = rendered_text.replace(placeholder, value)
        
        # Render rich card if present
        rich_card = None
        if self.rich_card_template:
            rich_card = self._render_rich_card(variable_values)
        
        # Render suggestions if present
        suggestions = []
        if self.suggestions_template:
            suggestions = self._render_suggestions(variable_values)
        
        # Track usage
        self.usage_count += 1
        self.last_used_at = datetime.utcnow()
        
        return MessageContent(
            text=rendered_text,
            rich_card=rich_card,
            suggestions=suggestions,
        )
    
    def add_variable(
        self,
        name: str,
        description: str,
        required: bool = True,
        default_value: Optional[str] = None,
    ) -> None:
        """
        Add a variable to the template
        
        Args:
            name: Variable name
            description: Variable description
            required: Whether variable is required
            default_value: Default value if not provided
        """
        if self.status != TemplateStatus.DRAFT:
            raise ValueError("Cannot modify approved template")
        
        # Check if variable already exists
        if any(v.name == name for v in self.variables):
            raise ValueError(f"Variable '{name}' already exists")
        
        self.variables.append(
            TemplateVariable(
                name=name,
                description=description,
                required=required,
                default_value=default_value,
            )
        )
        self.updated_at = datetime.utcnow()
    
    def set_rich_card(
        self,
        title: Optional[str] = None,
        description: Optional[str] = None,
        media_url: Optional[str] = None,
    ) -> None:
        """
        Set rich card template
        
        Args:
            title: Card title (can include variables)
            description: Card description (can include variables)
            media_url: Media URL (can include variables)
        """
        if self.status != TemplateStatus.DRAFT:
            raise ValueError("Cannot modify approved template")
        
        self.rich_card_template = {
            "title": title,
            "description": description,
            "media_url": media_url,
        }
        self.updated_at = datetime.utcnow()
    
    def add_suggestion(
        self,
        suggestion_type: str,
        text: str,
        **kwargs,
    ) -> None:
        """
        Add a suggested action to the template
        
        Args:
            suggestion_type: Type (reply, url, dial)
            text: Display text (can include variables)
            **kwargs: Additional parameters (url, phone_number, etc.)
        """
        if self.status != TemplateStatus.DRAFT:
            raise ValueError("Cannot modify approved template")
        
        suggestion = {
            "type": suggestion_type,
            "text": text,
            **kwargs,
        }
        self.suggestions_template.append(suggestion)
        self.updated_at = datetime.utcnow()
    
    def submit_for_approval(self) -> None:
        """Submit template for approval"""
        if self.status != TemplateStatus.DRAFT:
            raise ValueError("Template is not in draft status")
        
        self._validate_template()
        self.status = TemplateStatus.PENDING_APPROVAL
        self.updated_at = datetime.utcnow()
    
    def approve(self) -> None:
        """Approve template for use"""
        if self.status != TemplateStatus.PENDING_APPROVAL:
            raise ValueError("Template is not pending approval")
        
        self.status = TemplateStatus.APPROVED
        self.updated_at = datetime.utcnow()
    
    def reject(self, reason: str) -> None:
        """
        Reject template
        
        Args:
            reason: Rejection reason
        """
        if self.status != TemplateStatus.PENDING_APPROVAL:
            raise ValueError("Template is not pending approval")
        
        self.status = TemplateStatus.REJECTED
        self.updated_at = datetime.utcnow()
    
    def archive(self) -> None:
        """Archive template"""
        self.status = TemplateStatus.ARCHIVED
        self.updated_at = datetime.utcnow()
    
    def is_approved(self) -> bool:
        """Check if template is approved"""
        return self.status == TemplateStatus.APPROVED
    
    def extract_variables(self) -> Set[str]:
        """
        Extract all variables from content
        
        Returns:
            Set of variable names found in content
        """
        pattern = r'\{\{(\w+)\}\}'
        matches = re.findall(pattern, self.content)
        return set(matches)
    
    def _validate_template(self) -> None:
        """Validate template content"""
        # Check content length
        if len(self.content) > 1024:
            raise ValueError("Template content exceeds 1024 characters")
        
        # Extract variables from content
        content_vars = self.extract_variables()
        
        # Check all content variables are declared
        declared_vars = {v.name for v in self.variables}
        undeclared = content_vars - declared_vars
        if undeclared:
            raise ValueError(
                f"Undeclared variables in content: {', '.join(undeclared)}"
            )
        
        # Warn about unused declared variables
        unused = declared_vars - content_vars
        if unused:
            # Log warning (in production, use proper logging)
            pass
    
    def _validate_variables(self, variable_values: Dict[str, str]) -> None:
        """Validate all required variables are provided"""
        for var in self.variables:
            if var.required and var.name not in variable_values:
                if var.default_value is None:
                    raise ValueError(
                        f"Required variable '{var.name}' not provided"
                    )
    
    def _render_rich_card(
        self,
        variable_values: Dict[str, str],
    ) -> Optional[RichCard]:
        """Render rich card template with variables"""
        if not self.rich_card_template:
            return None
        
        def substitute(text: Optional[str]) -> Optional[str]:
            if not text:
                return None
            result = text
            for var_name, value in variable_values.items():
                placeholder = f"{{{{{var_name}}}}}"
                result = result.replace(placeholder, value)
            return result
        
        return RichCard(
            title=substitute(self.rich_card_template.get("title")),
            description=substitute(self.rich_card_template.get("description")),
            media_url=substitute(self.rich_card_template.get("media_url")),
        )
    
    def _render_suggestions(
        self,
        variable_values: Dict[str, str],
    ) -> List[SuggestedAction]:
        """Render suggestions template with variables"""
        suggestions = []
        
        for suggestion_template in self.suggestions_template:
            # Substitute variables in all string fields
            suggestion_data = {}
            for key, value in suggestion_template.items():
                if isinstance(value, str):
                    result = value
                    for var_name, var_value in variable_values.items():
                        placeholder = f"{{{{{var_name}}}}}"
                        result = result.replace(placeholder, var_value)
                    suggestion_data[key] = result
                else:
                    suggestion_data[key] = value
            
            suggestions.append(SuggestedAction(**suggestion_data))
        
        return suggestions
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize template to dictionary"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "content": self.content,
            "status": self.status,
            "variables": [
                {
                    "name": v.name,
                    "description": v.description,
                    "required": v.required,
                    "default_value": v.default_value,
                }
                for v in self.variables
            ],
            "rich_card_template": self.rich_card_template,
            "suggestions_template": self.suggestions_template,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "language": self.language,
            "usage_count": self.usage_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
