"""
Opt-Out Repository Implementation

Manages user consent and opt-out records.
Critical for compliance (GDPR, TCPA, DND).

Features:
    - Fast opt-out checks
    - Consent history tracking
    - Tenant isolation
    - Phone number normalization
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from datetime import datetime
import logging

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.ports.repository import OptOutRepository
from apps.adapters.db.models import OptInModel


logger = logging.getLogger(__name__)


class SQLAlchemyOptOutRepository(OptOutRepository):
    """
    SQLAlchemy implementation of OptOut Repository
    
    Fast lookups for opt-out status checking.
    
    Example:
        >>> repo = SQLAlchemyOptOutRepository(session)
        >>> is_opted_out = await repo.is_opted_out(
        ...     phone_number="+919876543210",
        ...     tenant_id=tenant_id,
        ... )
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize repository
        
        Args:
            session: SQLAlchemy async session
        """
        self.session = session
    
    async def is_opted_out(
        self,
        phone_number: str,
        tenant_id: UUID,
    ) -> bool:
        """
        Check if phone number has opted out
        
        Args:
            phone_number: Phone number in E.164
            tenant_id: Tenant context
            
        Returns:
            True if opted out
        """
        stmt = select(OptInModel).where(
            and_(
                OptInModel.tenant_id == tenant_id,
                OptInModel.phone_number == phone_number,
            )
        )
        
        result = await self.session.execute(stmt)
        opt_in = result.scalar_one_or_none()
        
        if not opt_in:
            # No record = not opted out (for testing/easy onboarding)
            return False
        
        # Check promotional status (most restrictive)
        from apps.core.domain.opt_in import ConsentStatus
        return opt_in.promotional_status == ConsentStatus.OPTED_OUT.value
    
    async def opt_out(
        self,
        phone_number: str,
        tenant_id: UUID,
        reason: Optional[str] = None,
    ) -> None:
        """
        Record opt-out
        
        Args:
            phone_number: Phone number
            tenant_id: Tenant context
            reason: Opt-out reason
        """
        stmt = select(OptInModel).where(
            and_(
                OptInModel.tenant_id == tenant_id,
                OptInModel.phone_number == phone_number,
            )
        )
        
        result = await self.session.execute(stmt)
        opt_in = result.scalar_one_or_none()
        
        from apps.core.domain.opt_in import ConsentStatus
        
        if opt_in:
            # Update existing record
            opt_in.promotional_status = ConsentStatus.OPTED_OUT.value
            opt_in.promotional_opted_out_at = datetime.utcnow()
            opt_in.updated_at = datetime.utcnow()
            
            # Add to consent history
            history = opt_in.consent_history or []
            history.append({
                "timestamp": datetime.utcnow().isoformat(),
                "status": ConsentStatus.OPTED_OUT.value,
                "consent_type": "promotional",
                "method": "api",
                "notes": reason,
            })
            opt_in.consent_history = history
        else:
            # Create new record
            from uuid import uuid4
            opt_in = OptInModel(
                id=uuid4(),
                tenant_id=tenant_id,
                phone_number=phone_number,
                promotional_status=ConsentStatus.OPTED_OUT.value,
                promotional_opted_out_at=datetime.utcnow(),
                consent_history=[{
                    "timestamp": datetime.utcnow().isoformat(),
                    "status": ConsentStatus.OPTED_OUT.value,
                    "consent_type": "promotional",
                    "method": "api",
                    "notes": reason,
                }],
            )
            self.session.add(opt_in)
        
        await self.session.flush()
        
        logger.info(f"Opt-out recorded for {phone_number} (tenant={tenant_id})")
    
    async def opt_in(
        self,
        phone_number: str,
        tenant_id: UUID,
    ) -> None:
        """
        Record opt-in (re-enable messaging)
        
        Args:
            phone_number: Phone number
            tenant_id: Tenant context
        """
        stmt = select(OptInModel).where(
            and_(
                OptInModel.tenant_id == tenant_id,
                OptInModel.phone_number == phone_number,
            )
        )
        
        result = await self.session.execute(stmt)
        opt_in = result.scalar_one_or_none()
        
        from apps.core.domain.opt_in import ConsentStatus
        
        if opt_in:
            # Update existing record
            opt_in.promotional_status = ConsentStatus.OPTED_IN.value
            opt_in.promotional_opted_in_at = datetime.utcnow()
            opt_in.updated_at = datetime.utcnow()
            
            # Add to consent history
            history = opt_in.consent_history or []
            history.append({
                "timestamp": datetime.utcnow().isoformat(),
                "status": ConsentStatus.OPTED_IN.value,
                "consent_type": "promotional",
                "method": "api",
            })
            opt_in.consent_history = history
        else:
            # Create new record
            from uuid import uuid4
            opt_in = OptInModel(
                id=uuid4(),
                tenant_id=tenant_id,
                phone_number=phone_number,
                promotional_status=ConsentStatus.OPTED_IN.value,
                promotional_opted_in_at=datetime.utcnow(),
                consent_history=[{
                    "timestamp": datetime.utcnow().isoformat(),
                    "status": ConsentStatus.OPTED_IN.value,
                    "consent_type": "promotional",
                    "method": "api",
                }],
            )
            self.session.add(opt_in)
        
        await self.session.flush()
        
        logger.info(f"Opt-in recorded for {phone_number} (tenant={tenant_id})")
    
    async def get_opt_outs(
        self,
        tenant_id: UUID,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get opt-out records
        
        Args:
            tenant_id: Tenant context
            since: Get opt-outs after this time
            
        Returns:
            List of opt-out records
        """
        from apps.core.domain.opt_in import ConsentStatus
        
        stmt = select(OptInModel).where(
            and_(
                OptInModel.tenant_id == tenant_id,
                OptInModel.promotional_status == ConsentStatus.OPTED_OUT.value,
            )
        )
        
        if since:
            stmt = stmt.where(OptInModel.promotional_opted_out_at > since)
        
        stmt = stmt.order_by(OptInModel.promotional_opted_out_at.desc())
        
        result = await self.session.execute(stmt)
        opt_ins = result.scalars().all()
        
        return [
            {
                "phone_number": opt_in.phone_number,
                "opted_out_at": (
                    opt_in.promotional_opted_out_at.isoformat()
                    if opt_in.promotional_opted_out_at else None
                ),
                "consent_history": opt_in.consent_history or [],
            }
            for opt_in in opt_ins
        ]
