from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Tool, EquipmentType, Payment, Sale, Customer, Supplier, SaleItem
from django.utils import timezone
import json

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ["id", "email", "name", "phone", "role", "password"]
        read_only_fields = ["id", "role"]

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

class ToolSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    equipment_type_name = serializers.CharField(source="equipment_type.name", read_only=True)
    equipment_type_id = serializers.CharField(source="equipment_type.id", read_only=True)
    box_type = serializers.CharField(source="description", read_only=True)  # Map description to box_type for frontend
    invoice_no = serializers.CharField(source="invoice_number", read_only=True)  # Map invoice_number to invoice_no

    class Meta:
        model = Tool
        fields = [
            "id",
            "name",
            "code",
            "category",
            "description",
            "box_type",  # Added for frontend compatibility
            "cost",
            "stock",
            "supplier",
            "supplier_name",
            "equipment_type",  
            "equipment_type_name",  
            "equipment_type_id",  
            "is_enabled",
            "invoice_number",
            "invoice_no",  # Added for frontend compatibility
            "date_added",
            "expiry_date",  # NEW: Added expiry_date
            "serials",
            "available_serials",  # NEW: Added available_serials
            "sold_serials",       # NEW: Added sold_serials
        ]
        extra_kwargs = {
            'expiry_date': {'required': False, 'allow_null': True}
        }

    def validate_serials(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Serials must be a list.")
        if not all(isinstance(s, str) for s in value):
            raise serializers.ValidationError("Each serial must be a string.")
        return value

    def validate_expiry_date(self, value):
        """Validate that expiry date is not in the past when creating/updating"""
        if value and value < timezone.now().date():
            raise serializers.ValidationError("Expiry date cannot be in the past.")
        return value

class EquipmentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentType  
        fields = ["id", "name", "default_cost", "naira_cost", "category", "description", "invoice_number", "created_at"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = "__all__"

class SaleItemSerializer(serializers.ModelSerializer):
    tool_id = serializers.PrimaryKeyRelatedField(
        queryset=Tool.objects.all(), source="tool", write_only=True
    )
    
    # NEW: Add computed fields for frontend
    serial_set = serializers.SerializerMethodField()
    datalogger_serial = serializers.SerializerMethodField()
    invoice_number = serializers.SerializerMethodField()
    assigned_tool_id = serializers.CharField(required=False, allow_blank=True)
    import_invoice = serializers.CharField(required=False, allow_blank=True, allow_null=True)  # NEW: Add import_invoice field

    class Meta:
        model = SaleItem
        fields = [
            'id', 'tool_id', 'equipment', 'cost', 'category', 
            'serial_number', 'serial_set', 'datalogger_serial', 
            'invoice_number', 'assigned_tool_id', 'import_invoice'  # NEW: Added import_invoice
        ]
        read_only_fields = ['id']

    def get_serial_set(self, obj):
        """Convert serial_number to serial_set array for frontend"""
        if obj.serial_number:
            # If it's a JSON string, parse it, otherwise treat as single serial
            try:
                serials = json.loads(obj.serial_number)
                if isinstance(serials, list):
                    return serials
            except (json.JSONDecodeError, TypeError):
                pass
            # Return as single item array
            return [obj.serial_number]
        return []

    def get_datalogger_serial(self, obj):
        """Extract datalogger serial from tool if available"""
        if obj.tool and hasattr(obj.tool, 'datalogger_serial'):
            return obj.tool.datalogger_serial
        return None

    def get_invoice_number(self, obj):
        """Get import invoice from sale"""
        if obj.sale and obj.sale.invoice_number:
            return obj.sale.invoice_number
        return None

    def create(self, validated_data):
        # Get a random serial number if not provided
        tool = validated_data.get('tool')
        if tool and not validated_data.get('serial_number'):
            random_serial = tool.get_random_serial()
            if random_serial:
                validated_data['serial_number'] = random_serial
                
        return super().create(validated_data)


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    sold_by = serializers.CharField(source="staff.email", read_only=True)
    date_sold = serializers.DateField(format='%Y-%m-%d')
    import_invoice = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Sale
        fields = [
            "id",
            "staff",
            "sold_by",
            "name",
            "phone",
            "state",
            "items",
            "total_cost",
            "date_sold",
            "invoice_number",
            "payment_plan",
            'initial_deposit',
            'payment_months',
            "expiry_date",
            "payment_status",
            "import_invoice",
        ]
        read_only_fields = ["staff", "sold_by", "date_sold", "invoice_number", "payment_status"]

    def validate(self, data):
        """
        Custom validation for payment plan fields
        """
        payment_plan = data.get('payment_plan')
        initial_deposit = data.get('initial_deposit')
        payment_months = data.get('payment_months')
        
        # If payment plan is "Yes", require installment fields
        if payment_plan == "Yes":
            if not initial_deposit:
                raise serializers.ValidationError({
                    "initial_deposit": "Initial deposit is required for installment plan."
                })
            if not payment_months:
                raise serializers.ValidationError({
                    "payment_months": "Payment months are required for installment plan."
                })
        # If payment plan is "No", clear installment fields
        elif payment_plan == "No":
            data['initial_deposit'] = None
            data['payment_months'] = None
        
        return data

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        user = self.context["request"].user
        validated_data["staff"] = user
        
        # Extract import_invoice from request data if available
        request = self.context.get('request')
        if request and hasattr(request, 'data'):
            import_invoice = request.data.get('import_invoice')
            if import_invoice:
                validated_data['import_invoice'] = import_invoice
        
        # Create the sale
        sale = Sale.objects.create(**validated_data)
        
        # Create sale items
        for item_data in items_data:
            # Extract frontend-specific fields
            serial_set = item_data.pop('serial_set', None)
            datalogger_serial = item_data.pop('datalogger_serial', None)
            assigned_tool_id = item_data.pop('assigned_tool_id', None)
            invoice_number = item_data.pop('invoice_number', None)
            import_invoice = item_data.pop('import_invoice', None)
            
            # Handle serial_set - convert to serial_number
            if serial_set and isinstance(serial_set, list):
                if len(serial_set) == 1:
                    item_data['serial_number'] = serial_set[0]
                else:
                    item_data['serial_number'] = json.dumps(serial_set)
            
            # Store assigned_tool_id
            if assigned_tool_id:
                item_data['assigned_tool_id'] = assigned_tool_id
            
            # Store import_invoice in sale item
            if import_invoice:
                item_data['import_invoice'] = import_invoice
            
            SaleItem.objects.create(sale=sale, **item_data)
            
        return sale

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        
        # Update sale fields including import_invoice
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update items if provided
        if items_data is not None:
            # Delete existing items and create new ones
            instance.items.all().delete()
            for item_data in items_data:
                SaleItem.objects.create(sale=instance, **item_data)
                
        return instance
    
class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "phone", "email", "state", "is_activated"]

class CustomerOwingSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='user.id', read_only=True) if not serializers.CharField else serializers.CharField(read_only=True)
    
    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'email', 'phone', 'total_selling_price', 
            'amount_paid', 'amount_left', 'date_last_paid', 
            'date_next_installment', 'status', 'progress'
        ]
    
    def to_representation(self, instance):
        """Convert the data to match frontend expectations"""
        data = super().to_representation(instance)
        
        # Convert field names to match frontend camelCase
        data['totalSellingPrice'] = float(data.pop('total_selling_price'))
        data['amountPaid'] = float(data.pop('amount_paid'))
        data['amountLeft'] = float(data.pop('amount_left'))
        data['dateLastPaid'] = data.pop('date_last_paid')
        data['dateNextInstallment'] = data.pop('date_next_installment')
        
        # Ensure ID is string format for frontend
        data['id'] = str(instance.id)
        
        return data

class PaymentSerializer(serializers.ModelSerializer):
    sale = serializers.PrimaryKeyRelatedField(
        queryset=Sale.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Payment
        fields = [
            "id",
            "customer",
            "sale",
            "amount",
            "payment_method",
            "payment_reference",
            "payment_date",
            "status",
        ]
        read_only_fields = ["customer", "payment_date", "status"]

    def create(self, validated_data):
        user = self.context["request"].user
        validated_data["customer"] = user
        payment = super().create(validated_data)
        payment.status = "completed"
        payment.save()

        if payment.sale:
            sale = payment.sale
            sale.payment_status = "completed"
            sale.save()

        return payment