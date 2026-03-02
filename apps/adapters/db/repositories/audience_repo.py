"""
Audience Repository

PHASE 3: Contacts are now stored in the `audience_contacts` table, not as a
JSON blob on the audiences row.

Key API change:
    BEFORE: save() serialised contacts into a JSON column
    AFTER:  save() upsertes the audience row + bulk-inserts AudienceContactModel rows

    BEFORE: get_phone_numbers() returned a list by reading the JSON blob in one shot
    AFTER:  stream_contacts() is an async generator, yielding batches of
            BATCH_SIZE rows using keyset pagination on (audience_id, id).
            Memory usage is bounded to one batch at a time regardless of audience size.

Features:
    - CRUD operations for audiences (metadata only — no contacts in the row)
    - stream_contacts() for memory-safe high-volume dispatch
    - bulk_add_contacts() for efficient CSV import
    - Contact deduplication via database UNIQUE constraint
    - get_phone_numbers() retained for small-audience API use (collects stream)
"""

from typing import AsyncGenerator, List, Optional
from uuid import UUID
import logging

from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.core.domain.audience import Audience, AudienceType, AudienceStatus, Contact
from apps.adapters.db.models import AudienceModel, AudienceContactModel


logger = logging.getLogger(__name__)

# Contacts fetched per keyset page during streaming
_STREAM_BATCH_SIZE = 1_000


class AudienceRepository:
    """
    Repository for audience persistence.

    Contacts are stored in the audience_contacts table, NOT on the audience row.
    Use stream_contacts() for high-volume campaign dispatch.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def save(self, audience: Audience) -> None:
        """
        Persist an audience and its contacts.

        Strategy:
          1. UPSERT the audience row (INSERT … ON CONFLICT UPDATE).
          2. Bulk-insert new contact rows with INSERT … ON CONFLICT DO NOTHING
             so deduplication is handled atomically at the DB layer.

        Note: existing contacts are NOT deleted — this is append-only.
        Call remove_contacts() first if you need to clear before re-upload.
        """
        # 1. Upsert the audience metadata row
        stmt = select(AudienceModel).where(AudienceModel.id == audience.id)
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.name = audience.name
            existing.status = audience.status.value
            existing.description = audience.description
            existing.tags = audience.tags
            existing.query = audience.query
            existing.total_contacts = audience.total_contacts
            existing.valid_contacts = audience.valid_contacts
            existing.invalid_contacts = audience.invalid_contacts
            existing.last_used_at = audience.last_used_at
            existing.updated_at = audience.updated_at
        else:
            model = AudienceModel(
                id=audience.id,
                tenant_id=audience.tenant_id,
                name=audience.name,
                audience_type=audience.audience_type.value,
                status=audience.status.value,
                description=audience.description,
                tags=audience.tags,
                query=audience.query,
                total_contacts=audience.total_contacts,
                valid_contacts=audience.valid_contacts,
                invalid_contacts=audience.invalid_contacts,
                created_at=audience.created_at,
                updated_at=audience.updated_at,
                last_used_at=audience.last_used_at,
            )
            self.session.add(model)
            # Flush so the FK exists before we insert contacts
            await self.session.flush()

        # 2. Bulk-insert contacts if any are present on the domain object
        if audience.contacts:
            await self.bulk_add_contacts(audience.id, audience.contacts)

    async def bulk_add_contacts(
        self,
        audience_id: UUID,
        contacts: List[Contact],
    ) -> int:
        """
        Bulk-insert contacts into audience_contacts.

        Uses PostgreSQL INSERT … ON CONFLICT DO NOTHING so re-uploading
        an audience or re-running a migration is always safe.

        Returns:
            Number of rows actually inserted (conflicts excluded).
        """
        if not contacts:
            return 0

        rows = [
            {
                "audience_id": audience_id,
                "phone_number": c.phone_number,
                "variables": getattr(c, "variables", None) or None,
                "metadata_": c.metadata if c.metadata else {},
            }
            for c in contacts
        ]

        # pg_insert gives us ON CONFLICT DO NOTHING natively
        stmt = (
            pg_insert(AudienceContactModel)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["audience_id", "phone_number"]
            )
        )
        result = await self.session.execute(stmt)
        return result.rowcount

    async def remove_contacts(self, audience_id: UUID) -> int:
        """Delete all contacts for an audience (needed before full re-upload)."""
        stmt = delete(AudienceContactModel).where(
            AudienceContactModel.audience_id == audience_id
        )
        result = await self.session.execute(stmt)
        return result.rowcount

    # ------------------------------------------------------------------
    # Read operations — metadata
    # ------------------------------------------------------------------

    async def get_by_id(
        self,
        audience_id: UUID,
        tenant_id: Optional[UUID] = None,
    ) -> Optional[Audience]:
        """
        Load audience metadata by ID.

        Contacts are NOT loaded here — use stream_contacts() for dispatch.
        """
        stmt = select(AudienceModel).where(AudienceModel.id == audience_id)
        if tenant_id:
            stmt = stmt.where(AudienceModel.tenant_id == tenant_id)

        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_tenant(
        self,
        tenant_id: UUID,
        limit: int = 100,
        offset: int = 0,
        status: Optional[AudienceStatus] = None,
        audience_type: Optional[AudienceType] = None,
    ) -> List[Audience]:
        """Return audience metadata list for a tenant (no contacts loaded)."""
        stmt = select(AudienceModel).where(AudienceModel.tenant_id == tenant_id)

        if status:
            stmt = stmt.where(AudienceModel.status == status.value)
        if audience_type:
            stmt = stmt.where(AudienceModel.audience_type == audience_type.value)

        stmt = stmt.order_by(AudienceModel.created_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def count_by_tenant(
        self,
        tenant_id: UUID,
        status: Optional[AudienceStatus] = None,
        audience_type: Optional[AudienceType] = None,
    ) -> int:
        stmt = select(func.count()).select_from(AudienceModel)
        stmt = stmt.where(AudienceModel.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(AudienceModel.status == status.value)
        if audience_type:
            stmt = stmt.where(AudienceModel.audience_type == audience_type.value)

        result = await self.session.execute(stmt)
        return result.scalar()

    async def delete(self, audience_id: UUID) -> None:
        """Delete audience (CASCADE removes audience_contacts rows automatically)."""
        stmt = select(AudienceModel).where(AudienceModel.id == audience_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)

    async def get_active_audiences(self, tenant_id: UUID) -> List[Audience]:
        """Get all active audiences for a tenant (metadata only)."""
        return await self.get_by_tenant(
            tenant_id=tenant_id,
            status=AudienceStatus.ACTIVE,
            limit=1000,
        )

    # ------------------------------------------------------------------
    # Streaming contacts — the safe path for high-volume dispatch
    # ------------------------------------------------------------------

    async def stream_contacts(
        self,
        audience_id: UUID,
        batch_size: int = _STREAM_BATCH_SIZE,
    ) -> AsyncGenerator[List[AudienceContactModel], None]:
        """
        Stream contacts for an audience in batches using keyset pagination.

        NEVER loads the full contact list into memory. Each `yield` produces
        at most `batch_size` rows, and the generator stops when all rows
        have been consumed.

        Keyset pagination on (audience_id, id) is O(log N) per page —
        far more efficient than OFFSET for large audiences.

        Usage (in orchestrator):
            async for batch in uow.audiences.stream_contacts(audience_id):
                for row in batch:
                    phone    = row.phone_number
                    variables = row.variables or []
                    ...

        Args:
            audience_id: The audience to stream contacts for
            batch_size:  Rows per page (default 1 000)

        Yields:
            List[AudienceContactModel] — one batch per iteration
        """
        last_id: Optional[UUID] = None
        total_streamed = 0

        while True:
            stmt = (
                select(AudienceContactModel)
                .where(AudienceContactModel.audience_id == audience_id)
                .order_by(AudienceContactModel.id)
                .limit(batch_size)
            )

            # Keyset: skip everything we've already yielded
            if last_id is not None:
                stmt = stmt.where(AudienceContactModel.id > last_id)

            result = await self.session.execute(stmt)
            rows = result.scalars().all()

            if not rows:
                break

            total_streamed += len(rows)
            last_id = rows[-1].id

            logger.debug(
                "stream_contacts: audience=%s batch=%d total_so_far=%d",
                audience_id, len(rows), total_streamed,
            )

            yield list(rows)

    # ------------------------------------------------------------------
    # Legacy helper — kept for small-audience API use only
    # ------------------------------------------------------------------

    async def get_phone_numbers(self, audience_id: UUID) -> List[str]:
        """
        Return all phone numbers for an audience as a list.

        WARNING: loads all contacts into memory. Only use for small audiences
        (e.g., preview in the API layer). For campaign dispatch, use
        stream_contacts() instead.
        """
        phones: List[str] = []
        async for batch in self.stream_contacts(audience_id, batch_size=5_000):
            phones.extend(row.phone_number for row in batch)
        return phones

    async def count_contacts(self, audience_id: UUID) -> int:
        """Count contacts for an audience (does not load rows)."""
        stmt = (
            select(func.count())
            .select_from(AudienceContactModel)
            .where(AudienceContactModel.audience_id == audience_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar()

    # ------------------------------------------------------------------
    # Domain ↔ ORM conversion
    # ------------------------------------------------------------------

    def _to_domain(self, model: AudienceModel) -> Audience:
        """
        Convert database model to domain model (metadata only — no contacts).

        The domain Audience.contacts list is intentionally left empty here.
        To populate it (e.g., for small preview requests), call
        stream_contacts() and convert rows to Contact objects yourself.
        """
        audience = Audience(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            audience_type=AudienceType(model.audience_type),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

        audience.status = AudienceStatus(model.status)
        audience.description = model.description
        audience.tags = model.tags or []
        audience.query = model.query
        audience.total_contacts = model.total_contacts
        audience.valid_contacts = model.valid_contacts
        audience.invalid_contacts = model.invalid_contacts
        audience.last_used_at = model.last_used_at

        # contacts list is intentionally empty — stream from audience_contacts table
        return audience
