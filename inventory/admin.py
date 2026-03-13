from django.contrib import admin
from .models import User, Tool, Payment, Customer, Sale, EquipmentType,CodeBatch, ActivationCode


class ToolAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'category', 'equipment_type', 'stock', 'cost']
    list_filter = ['category', 'equipment_type', 'supplier']
    search_fields = ['name', 'code']

class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'default_cost', 'category', 'invoice_number', 'created_at']  # Added invoice_number
    search_fields = ['name', 'invoice_number']    
    list_filter = ['category', 'invoice_number']    

@admin.register(CodeBatch)
class CodeBatchAdmin(admin.ModelAdmin):
    list_display = ['batch_number', 'supplier', 'received_date']

@admin.register(ActivationCode)
class ActivationCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'receiver_serial', 'customer', 'sale', 'status', 'expiry_date']
    list_filter = ['status', 'expiry_date']
    search_fields = ['code', 'receiver_serial']


admin.site.register(User)
admin.site.register(Tool, ToolAdmin)
admin.site.register(EquipmentType, EquipmentTypeAdmin)
admin.site.register(Payment)
admin.site.register(Customer)
admin.site.register(Sale) 