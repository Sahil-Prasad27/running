import json

ERROR_REGISTRY = {}

class VinylException(Exception):
    errorCode = "VINYL_EXCEPTION"
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.errorCode = cls.__name__.upper()
        if cls.__name__ != 'VinylException' and not cls.__name__.startswith('_'):
            ERROR_REGISTRY[cls.errorCode] = cls
    def __init__(self, message="", context=None):
        self.message = message or self.__class__.__name__
        self.context = context or {}
        super().__init__(self.message)
    def __str__(self):
        return self.message
    def to_dict(self):
        return {"errorCode": self.errorCode, "message": self.message, "context": self.context}

class CatalogueException(VinylException): pass
class DuplicateCatalogueException(CatalogueException): pass
class UnknownArtistException(CatalogueException): pass
class CounterfeitPressingException(CatalogueException): pass
class ImageNotFoundException(CatalogueException): pass

class GradingException(VinylException): pass
class InvalidGradeException(GradingException): pass
class GradingMismatchException(GradingException): pass
class DowngradeNotAuthorisedException(GradingException): pass

class TradeInException(VinylException): pass
class ValuationOverrideException(TradeInException): pass
class InsufficientHistoryException(TradeInException): pass

class WantlistException(VinylException): pass
class ReservationConflictException(WantlistException): pass
class WantlistInactiveException(WantlistException): pass
class NotificationFailureException(WantlistException): pass

class POSException(VinylException): pass
class OutOfStockException(POSException): pass
class DiscountAboveRoleCapException(POSException): pass
class TenderMismatchException(POSException): pass
class PaymentDeclinedException(POSException): pass
class ReservedForAnotherCustomerException(POSException): pass

class ConsignmentException(VinylException): pass
class TierOverlapException(ConsignmentException): pass
class BelowFloorSaleException(ConsignmentException): pass
class StatementPeriodOverlapException(ConsignmentException): pass

class ServiceException(VinylException): pass
class StatusTransitionInvalidException(ServiceException): pass
class AbandonmentPolicyException(ServiceException): pass
class QuoteRecalculationException(ServiceException): pass

class IOExceptionFamily(VinylException): pass
class CatalogLoadError(IOExceptionFamily): pass
class ImportSchemaMismatchException(IOExceptionFamily): pass
class PrinterOfflineException(IOExceptionFamily): pass
class ReceiptRenderError(IOExceptionFamily): pass
class StorageQuotaException(IOExceptionFamily): pass
class DatabaseTransactionException(IOExceptionFamily): pass


def exception_from_dict(data):
    cls = ERROR_REGISTRY.get(data.get('errorCode'), VinylException)
    return cls(data.get('message', ''), data.get('context', {}))
