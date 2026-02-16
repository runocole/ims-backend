from django.urls import path
from .views import (
    EmailLoginView, AddStaffView, StaffListView,
    ToolListCreateView, ToolDetailView, EquipmentTypeListView, EquipmentTypeDetailView,
    SaleListCreateView, SaleDetailView,
    PaymentListCreateView, PaymentDetailView,
    DashboardSummaryView, AddCustomerView, CustomerListView, send_sale_email, 
    SupplierListView, SupplierDetailView, equipment_by_invoice,
    ToolGetRandomSerialView, ToolSoldSerialsView,  ToolGroupedListView, ToolAssignRandomFromGroupView, CustomerOwingDataView, ImportCodesView,
    AssignCodeView, CustomerCodesView, GenerateEmergencyCodeView, AvailableCodesView, ReceiversNeedingCodesView,
)
urlpatterns = [
    # --- Auth ---
    path("auth/login/", EmailLoginView.as_view(), name="login"),
    path("auth/add-staff/", AddStaffView.as_view(), name="add-staff"),
    path("auth/staff/", StaffListView.as_view(), name="staff-list"),

    # --- Customers ---
    path("customers/add", AddCustomerView.as_view(), name="add-customer"),
    path("customers/", CustomerListView.as_view(), name="customers"),
     path('customer-owing/', CustomerOwingDataView.as_view(), name='customer-owing-data'),
    # --- Tools ---
    path("tools/", ToolListCreateView.as_view(), name="tools"),
    path("tools/<uuid:pk>/", ToolDetailView.as_view(), name="tool-detail"),
    path("tools/grouped/", ToolGroupedListView.as_view(), name="tool-grouped-list"),
    path("tools/assign-random/", ToolAssignRandomFromGroupView.as_view(), name="tool-assign-random"),
    path("tools/<uuid:pk>/get-random-serial/", ToolGetRandomSerialView.as_view(), name="tool-get-random-serial"),
    path("tools/<uuid:pk>/sold-serials/", ToolSoldSerialsView.as_view(), name="tool-sold-serials"),
  
    
    # Equipment Type
    path("equipment-types/", EquipmentTypeListView.as_view(), name="equipment-type-list"),
    path("equipment-types/<int:pk>/", EquipmentTypeDetailView.as_view(), name="equipment-type-detail"),
    path("equipment-types/by-invoice/", equipment_by_invoice, name="equipment-by-invoice"),

    # --- Sales ---
    path("sales/", SaleListCreateView.as_view(), name="sales"),
    path("sales/<int:pk>/", SaleDetailView.as_view(), name="sale-detail"),

    # --- Email --- 
    path('send-sale-email/', send_sale_email, name='send_sale_email'),

    # Payments
    path('payments/', PaymentListCreateView.as_view(), name='payments'),
    path('payments/<int:pk>/', PaymentDetailView.as_view(), name='payment-detail'),
    
    # Suppliers
    path('suppliers/', SupplierListView.as_view(), name='suppliers'),
    path('suppliers/<uuid:pk>/', SupplierDetailView.as_view(), name='supplier-detail'),

    # Dashboard
    path('dashboard/summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),

    # Code management URLs
    path('api/codes/import/', ImportCodesView.as_view(), name='import-codes'),
    path('api/codes/assign/', AssignCodeView.as_view(), name='assign-code'),
    path('api/codes/customer/', CustomerCodesView.as_view(), name='customer-codes'),
    path('api/codes/emergency/', GenerateEmergencyCodeView.as_view(), name='emergency-code'),
    path('api/codes/available/', AvailableCodesView.as_view(), name='available-codes'),
    path('api/codes/needing-codes/', ReceiversNeedingCodesView.as_view(), name='needing-codes'),
]