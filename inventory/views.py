from django.db import models, transaction
from rest_framework import generics, permissions, status
from django.contrib.auth import get_user_model
from .models import Tool, Payment, Sale, Customer, EquipmentType, Supplier, SaleItem, CodeBatch, ActivationCode, CodeAssignmentLog 
from .serializers import (
    UserSerializer, ToolSerializer, EquipmentTypeSerializer,
    PaymentSerializer, SaleSerializer, CustomerSerializer, SupplierSerializer, CustomerOwingSerializer,
    CodeBatchSerializer, ActivationCodeSerializer, CodeAssignmentLogSerializer
)
from .permissions import IsAdminOrStaff, IsOwnerOrAdmin
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Max, Q
from django.core.mail import send_mail, BadHeaderError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from datetime import timedelta
import secrets, uuid, traceback
import json
import pandas as pd
from io import BytesIO

User = get_user_model()


# ----------------------------
# STAFF MANAGEMENT
# ----------------------------
class AddStaffView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)

        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="staff",
            is_active=True,
        )

        try:
            send_mail(
                subject="Your Staff Account Details",
                message=f"Hello {name or 'Staff'},\n\nYour account has been created.\n\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            traceback.print_exc()

        return Response(
            {
                "id": user.id,
                "email": email,
                "name": user.name,
                "phone": user.phone,
                "detail": "Staff created successfully",
            },
            status=status.HTTP_201_CREATED,
        )


class StaffListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]

    def get_queryset(self):
        return User.objects.filter(role="staff")


# ----------------------------
# AUTHENTICATION
# ----------------------------
class EmailLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user or not user.check_password(password):
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        if not user.is_active:
            return Response({"detail": "User account is disabled."}, status=status.HTTP_403_FORBIDDEN)

        # Auto-activate customer on first login
        if user.role == "customer":
            try:
                customer = Customer.objects.get(user=user)
                if not customer.is_activated:
                    customer.is_activated = True
                    customer.save()
            except Customer.DoesNotExist:
                pass

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_200_OK,
        )


# ----------------------------
# CUSTOMERS
# ----------------------------
class AddCustomerView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        email = request.data.get("email")
        name = request.data.get("name")
        phone = request.data.get("phone")
        state = request.data.get("state")

        if not email:
            return Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email).exists():
            return Response({"detail": "User with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)

        password = secrets.token_urlsafe(10)
        user = User.objects.create_user(
            email=email,
            password=password,
            name=name or "",
            phone=phone or "",
            role="customer",
            is_active=True,
        )

        Customer.objects.create(
            user=user, name=name, phone=phone, state=state, email=email
        )

        try:
            send_mail(
                subject="Your Customer Account Details",
                message=f"Hello {name or 'Customer'},\n\nAn account has been created for you.\nEmail: {email}\nPassword: {password}",
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "runocole@gmail.com"),
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception as e:
            print("Failed to send email:", e)

        return Response(
            {"id": user.id, "email": email, "name": name, "phone": phone, "state": state},
            status=status.HTTP_201_CREATED,
        )


class CustomerListView(generics.ListAPIView):
    serializer_class = CustomerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "admin":
            return Customer.objects.all().order_by("-id")
        return Customer.objects.all().order_by("-id")
    
# ----------------------------
# CUSTOMER OWING/INSTALLMENT TRACKING
# ----------------------------
class CustomerOwingDataView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        try:
            # Get all customers
            customers = Customer.objects.all()
            
            # Calculate summary statistics
            total_selling_price = sum(customer.total_selling_price for customer in customers)
            total_amount_received = sum(customer.amount_paid for customer in customers)
            total_amount_left = sum(customer.amount_left for customer in customers)
            today = timezone.now().date()
            next_week = today + timedelta(days=7)
            
            upcoming_receivables = sum(
                customer.amount_left for customer in customers 
                if customer.date_next_installment and 
                customer.date_next_installment <= next_week and
                customer.status != 'fully-paid'
            )
            
            # Count overdue customers
            overdue_customers_count = customers.filter(
                status='overdue'
            ).count()
            
            # Prepare summary data
            summary = {
                "totalSellingPrice": float(total_selling_price),
                "totalAmountReceived": float(total_amount_received),
                "totalAmountLeft": float(total_amount_left),
                "upcomingReceivables": float(upcoming_receivables),
                "overdueCustomers": overdue_customers_count,
                "totalCustomers": customers.count()
            }
            
            # Serialize customer data
            customers_data = CustomerOwingSerializer(customers, many=True).data
            
            response_data = {
                "summary": summary,
                "customers": customers_data
            }
            
            return Response(response_data)
            
        except Exception as e:
            print(f"Error in CustomerOwingDataView: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": "Failed to fetch customer owing data"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
# ----------------------------
# TOOLS
# ----------------------------

class ToolListCreateView(generics.ListCreateAPIView):
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Tool.objects.select_related("supplier").order_by("-date_added")

        # Filter by category and equipment type if provided
        category = self.request.query_params.get('category')
        equipment_type = self.request.query_params.get('equipment_type')
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type:
            # For Receiver category, filter by description/box_type based on equipment_type
            if category == "Receiver":
                if equipment_type == "Base Only":
                    queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
                elif equipment_type == "Rover Only":
                    queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
                elif equipment_type == "Base & Rover Combo":
                    queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
                elif equipment_type == "Accessories":
                    queryset = queryset.filter(description__icontains="accessory")

        if getattr(user, "role", None) == "customer":
            queryset = queryset.filter(stock__gt=0, is_enabled=True)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, "role", None) == "customer":
            raise permissions.PermissionDenied("Customers cannot add tools.")
        
        # Initialize available_serials with serials if not provided
        tool_data = serializer.validated_data
        if 'available_serials' not in tool_data or not tool_data['available_serials']:
            if 'serials' in tool_data and tool_data['serials']:
                tool_data['available_serials'] = tool_data['serials'].copy()
                
        serializer.save()

# NEW: Get tools grouped by name for frontend display
class ToolGroupedListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        user = self.request.user
        category = request.query_params.get('category')
        equipment_type = request.query_params.get('equipment_type')
        
        # Start with base queryset
        queryset = Tool.objects.filter(stock__gt=0, is_enabled=True)
        
        if category:
            queryset = queryset.filter(category=category)
            
        if equipment_type and category == "Receiver":
            # Apply equipment type filtering for Receiver category
            if equipment_type == "Base Only":
                queryset = queryset.filter(description__icontains="base").exclude(description__icontains="rover")
            elif equipment_type == "Rover Only":
                queryset = queryset.filter(description__icontains="rover").exclude(description__icontains="base")
            elif equipment_type == "Base & Rover Combo":
                queryset = queryset.filter(description__icontains="base").filter(description__icontains="rover")
            elif equipment_type == "Accessories":
                queryset = queryset.filter(description__icontains="accessory")
        
        # Group tools by name and calculate total stock
        from django.db.models import Sum, Count
        grouped_tools = queryset.values('name', 'category', 'cost').annotate(
            total_stock=Sum('stock'),
            tool_count=Count('id'),
            available_serials_count=Sum('stock')  # Assuming each stock item has one serial
        ).order_by('name')
        
        # Convert to list and add additional info
        result = []
        for tool_group in grouped_tools:
            # Get one sample tool for additional fields
            sample_tool = queryset.filter(name=tool_group['name']).first()
            if sample_tool:
                result.append({
                    'name': tool_group['name'],
                    'category': tool_group['category'],
                    'cost': tool_group['cost'],
                    'total_stock': tool_group['total_stock'],
                    'tool_count': tool_group['tool_count'],
                    'description': sample_tool.description,
                    'supplier_name': sample_tool.supplier.name if sample_tool.supplier else None,
                    'group_id': f"group_{tool_group['name'].replace(' ', '_').lower()}"
                })
        
        return Response(result)

# NEW: Assign random tool from group
class ToolAssignRandomFromGroupView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        tool_name = request.data.get('tool_name')
        category = request.data.get('category')
        
        if not tool_name:
            return Response({"error": "Tool name is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Find all available tools with this name and category that have enough serials
        available_tools = Tool.objects.filter(
            name=tool_name, 
            category=category,
            stock__gt=0,
            is_enabled=True
        )
        
        # === ADD YOUR COMBO LOGIC HERE ===
        # Check if this is a combo equipment type
        if "combo" in tool_name.lower():
            # For combo, we need to assign both base and rover equipment
            base_tools = Tool.objects.filter(
                name__icontains="base only",  # Adjust based on your actual tool names
                category=category,
                stock__gt=0,
                is_enabled=True
            )
            rover_tools = Tool.objects.filter(
                name__icontains="rover only",  # Adjust based on your actual tool names  
                category=category,
                stock__gt=0,
                is_enabled=True
            )
            
            if not base_tools or not rover_tools:
                return Response(
                    {"error": "Complete base and rover sets not available for combo."},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get base serials (2 serials)
            base_tool = base_tools.first()
            base_serial_set = base_tool.get_random_serial_set()
            if not base_serial_set or len(base_serial_set) != 2:
                return Response(
                    {"error": "Failed to get complete base serial set."},
                    status=status.HTTP_404_NOT_FOUND
                )
            base_tool.decrease_stock()
            
            # Get rover serials (2 serials)  
            rover_tool = rover_tools.first()
            rover_serial_set = rover_tool.get_random_serial_set()
            if not rover_serial_set or len(rover_serial_set) != 2:
                return Response(
                    {"error": "Failed to get complete rover serial set."},
                    status=status.HTTP_404_NOT_FOUND
                )
            rover_tool.decrease_stock()
            
            # Combine all serials
            all_serials = base_serial_set + rover_serial_set
            
            return Response({
                "assigned_tool_id": f"combo_{base_tool.id}_{rover_tool.id}",
                "tool_name": tool_name,
                "serial_set": all_serials,  # This should be 4 serials total
                "serial_count": len(all_serials),
                "set_type": "Base & Rover Combo",
                "cost": str(float(base_tool.cost) + float(rover_tool.cost)),
                "description": "Base & Rover Combo Set",
                "remaining_stock": min(base_tool.stock, rover_tool.stock),
                "import_invoice": base_tool.invoice_number  # Use base tool invoice
            })
        
        # === NORMAL LOGIC FOR NON-COMBO EQUIPMENT ===
        # Filter tools that have enough serials for a complete set
        tools_with_enough_serials = []
        for tool in available_tools:
            set_count = tool.get_serial_set_count()
            if tool.available_serials and len(tool.available_serials) >= set_count:
                tools_with_enough_serials.append(tool)
        
        if not tools_with_enough_serials:
            return Response(
                {"error": f"No complete {tool_name} sets available in stock."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Select a random tool from available ones
        import random
        selected_tool = random.choice(tools_with_enough_serials)
        
        # Get a complete serial SET (2 or 4 serials depending on equipment type)
        serial_set = selected_tool.get_random_serial_set()
        
        if not serial_set:
            return Response(
                {"error": "Failed to get complete serial set from selected tool."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Update the stock count
        selected_tool.decrease_stock()
        
        return Response({
            "assigned_tool_id": selected_tool.id,
            "tool_name": selected_tool.name,
            "serial_set": serial_set,  # This is now an ARRAY of serials
            "serial_count": len(serial_set),
            "set_type": selected_tool.description,
            "cost": str(selected_tool.cost),
            "description": selected_tool.description,
            "remaining_stock": selected_tool.stock,
            "import_invoice": selected_tool.invoice_number
        })
    
# NEW: Get random serial number for a tool
class ToolGetRandomSerialView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        try:
            tool = get_object_or_404(Tool, pk=pk)
            
            if not tool.available_serials:
                return Response(
                    {"error": "No available serial numbers for this tool."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            random_serial = tool.get_random_serial()
            
            if not random_serial:
                return Response(
                    {"error": "Failed to get random serial number."},
                    status=status.HTTP_404_NOT_FOUND
                )
                
            return Response({
                "serial_number": random_serial,
                "tool_name": tool.name,
                "remaining_serials": len(tool.available_serials)
            })
            
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
class ToolDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Tool.objects.all()
    serializer_class = ToolSerializer
    permission_classes = [permissions.IsAuthenticated]


class ToolSoldSerialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, pk):
        tool = get_object_or_404(Tool, pk=pk)
        
        sold_serials = []
        for serial_info in tool.sold_serials or []:
            if isinstance(serial_info, dict):
                sold_serials.append({
                    'serial': serial_info.get('serial', 'Unknown'),
                    'sale_id': serial_info.get('sale_id'),
                    'customer_name': serial_info.get('customer_name', 'Unknown'),
                    'date_sold': serial_info.get('date_sold'),
                    'invoice_number': serial_info.get('invoice_number'),
                    'import_invoice': serial_info.get('import_invoice')  # NEW: Add import_invoice
                })
            else:
                # Handle case where serial_info is just a string
                sold_serials.append({
                    'serial': serial_info,
                    'sale_id': None,
                    'customer_name': 'Unknown',
                    'date_sold': None,
                    'invoice_number': None,
                    'import_invoice': None  # NEW: Add import_invoice
                })
                
        return Response(sold_serials)

# ----------------------------
# EQUIPMENT TYPE
# ----------------------------

class EquipmentTypeListView(generics.ListCreateAPIView):
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = EquipmentType.objects.all().order_by("category", "name")
        
        # Filter by invoice_number if provided
        invoice_number = self.request.query_params.get('invoice_number')
        if invoice_number:
            queryset = queryset.filter(invoice_number=invoice_number)
        
        # Filter by category if provided
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
            
        return queryset

class EquipmentTypeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = EquipmentType.objects.all()
    serializer_class = EquipmentTypeSerializer
    permission_classes = [permissions.AllowAny]

# NEW VIEW: Get equipment grouped by invoice
@api_view(['GET'])
def equipment_by_invoice(request):
    """
    Get equipment types grouped by invoice number with counts and totals
    """
    from django.db.models import F, FloatField
    from django.db.models.functions import Cast
    
    invoices = EquipmentType.objects.exclude(invoice_number__isnull=True)\
        .exclude(invoice_number__exact='')\
        .values('invoice_number')\
        .annotate(
            equipment_count=Count('id'),
            total_value=Sum('default_cost'),
            last_updated=Max('created_at')
        )\
        .order_by('-last_updated')
    
    return Response(list(invoices))

#-------------------
# SUPPLIERS
#--------------------
class SupplierListView(generics.ListCreateAPIView):
    queryset = Supplier.objects.all().order_by("name")
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]

class SupplierDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.AllowAny]

# ----------------------------
# SALES
# ----------------------------
class SaleListCreateView(generics.ListCreateAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "staff":
            return Sale.objects.filter(staff=user).order_by("-date_sold")
        elif user.role == "admin":
            return Sale.objects.all().order_by("-date_sold")
        return Sale.objects.none()

    def perform_create(self, serializer):
        # Get import_invoice from request data
        import_invoice = self.request.data.get('import_invoice')
        
        # Save the sale with staff and import_invoice
        sale = serializer.save(
            staff=self.request.user,
            import_invoice=import_invoice
        )
        
        # FIXED: Also update sale items with serial_set data
        if sale.items.exists():
            items_data = self.request.data.get('items', [])
            for i, item in enumerate(sale.items.all()):
                if i < len(items_data):
                    item_data = items_data[i]
                    # Save serial_set to the item
                    serial_set = item_data.get('serial_set')
                    if serial_set:
                        # Convert serial_set array to serial_number field
                        if isinstance(serial_set, list) and len(serial_set) > 0:
                            if len(serial_set) == 1:
                                item.serial_number = serial_set[0]
                            else:
                                item.serial_number = json.dumps(serial_set)
                        item.save()
        
        # Also update sale items with import_invoice if provided
        if import_invoice and sale.items.exists():
            sale.items.all().update(import_invoice=import_invoice)


class SaleDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role == "staff":
            return Sale.objects.filter(staff=user)
        elif user.role == "admin":
            return Sale.objects.all()
        return Sale.objects.none()

    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
        if user.role == "staff" and instance.staff != user:
            raise PermissionDenied("You can only edit your own sales.")
        return super().perform_update(serializer)

# ----------------------------
# EMAIL API
# ----------------------------
@api_view(["POST"])
@permission_classes([AllowAny])  
def send_sale_email(request):
    try:
        data = request.data
        send_mail(
            subject=data.get("subject", "Your Payment Link"),
            message=data.get("message", "Hello, your payment link will be available soon."),
            from_email="runocole@gmail.com",  
            recipient_list=[data.get("to_email")],
            fail_silently=False,
        )
        return Response({"message": "Email sent successfully!"})
    except Exception as e:
        return Response({"error": str(e)}, status=500)
# ----------------------------
# DASHBOARD SUMMARY
# ----------------------------
class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        total_sales = Sale.objects.count()
        total_revenue = Sale.objects.aggregate(total=Sum("total_cost"))["total"] or 0
        tools_count = Tool.objects.filter(stock__gt=0).count()
        staff_count = User.objects.filter(role="staff").count()
        active_customers = Customer.objects.filter(is_activated=True).count()

        today = timezone.now()
        month_start = today.replace(day=1)
        mtd_revenue = (
            Sale.objects.filter(date_sold__gte=month_start)
            .aggregate(total=Sum("total_cost"))
            .get("total")
            or 0
        )

        inventory_breakdown = []
        receiver_tools_breakdown = (
            Tool.objects
            .filter(category="Receiver", stock__gt=0)  
            .values("name")
            .annotate(count=Count("id"))
            .order_by("name")
        )
        
        for item in receiver_tools_breakdown:
            inventory_breakdown.append({
                "receiver_type": item["name"],
                "count": item["count"]
            })

        if not inventory_breakdown:
            inventory_breakdown.append({
                "receiver_type": "No receiver tools",
                "count": 0
            })

        # ✅ FIXED: Low stock should only show items that actually have stock
        low_stock_items = list(
            Tool.objects.filter(stock__lte=5, stock__gt=0)  # Only items with stock
            .values("id", "name", "code", "category", "stock")[:5]
        )

        # Top selling tools
        top_selling_tools = (
            SaleItem.objects.values("tool__name")
            .annotate(total_sold=Count("id"))
            .order_by("-total_sold")[:5]
        )

        # Recent sales
        recent_sales = Sale.objects.prefetch_related('items').order_by('-date_sold')[:10]
        recent_sales_data = []
        for sale in recent_sales:
            first_item = sale.items.first()
            tool_name = first_item.equipment if first_item else "No equipment"
            
            recent_sales_data.append({
                'invoice_number': sale.invoice_number,
                'customer_name': sale.name,
                'tool_name': tool_name,
                'cost_sold': sale.total_cost,
                'payment_status': sale.payment_status,
                'date_sold': sale.date_sold,
                'import_invoice': sale.import_invoice  # NEW: Add import_invoice to recent sales
            })

        # Expiring receivers - only show items with stock
        thirty_days_from_now = timezone.now().date() + timedelta(days=30)
        expiring_receivers = (
            Tool.objects
            .filter(
                category="Receiver",
                expiry_date__isnull=False,
                expiry_date__gt=timezone.now().date(),
                expiry_date__lte=thirty_days_from_now,
                stock__gt=0  # Only show items that are in stock
            )
            .values("name", "code", "expiry_date")
            .order_by("expiry_date")[:10]
        )
        
        expiring_receivers_data = []
        for receiver in expiring_receivers:
            expiring_receivers_data.append({
                "name": receiver["name"],
                "serialNumber": receiver["code"],
                "expirationDate": receiver["expiry_date"].isoformat() if receiver["expiry_date"] else None
            })

        return Response(
            {
                "totalTools": tools_count,
                "totalStaff": staff_count,
                "activeCustomers": active_customers,
                "mtdRevenue": mtd_revenue,
                "inventoryBreakdown": inventory_breakdown,
                "lowStockItems": low_stock_items,
                "topSellingTools": list(top_selling_tools),
                "recentSales": recent_sales_data,
                "expiringReceivers": expiring_receivers_data,
            }
        )

# ----------------------------
# PAYMENTS
# ----------------------------
class PaymentListCreateView(generics.ListCreateAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [permissions.IsAuthenticated]


class PaymentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [IsOwnerOrAdmin]

# ----------------------------
#  CODE MANAGEMENT VIEWS
# ----------------------------

import pandas as pd
from io import BytesIO
from django.db import transaction

# 1. IMPORT CODES FROM EXCEL
class ImportCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            excel_file = request.FILES.get('excel_file')
            batch_number = request.data.get('batch_number', f'CHINA-{timezone.now().strftime("%Y%m%d")}')
            supplier = request.data.get('supplier', 'China Supplier')
            
            if not excel_file:
                return Response(
                    {"error": "Excel file is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Read Excel file
            df = pd.read_excel(excel_file)
            
            # Expected columns: code, duration, serial_number (optional)
            required_columns = ['code', 'duration']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                return Response(
                    {"error": f"Missing columns: {', '.join(missing_columns)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create batch
            batch = CodeBatch.objects.create(
                batch_number=batch_number,
                supplier=supplier,
                notes=f"Imported {len(df)} codes from Excel"
            )
            
            # Import codes
            imported_count = 0
            assigned_count = 0
            
            for _, row in df.iterrows():
                code = str(row['code']).strip()
                duration = str(row['duration']).strip().lower()
                serial_number = str(row.get('serial_number', '')).strip() if pd.notna(row.get('serial_number')) else None
                
                # Map duration to valid choices
                duration_map = {
                    '2 weeks': '2weeks',
                    '1 month': '1month',
                    '3 months': '3months',
                    'unlimited': 'unlimited',
                    '2weeks': '2weeks',
                    '1month': '1month',
                    '3months': '3months',
                }
                
                duration = duration_map.get(duration, '3months')  # Default to 3months
                
                # Create code
                activation_code = ActivationCode.objects.create(
                    code=code,
                    duration=duration,
                    batch=batch,
                    receiver_serial=serial_number if serial_number else None
                )
                
                imported_count += 1
                
                # Auto-assign if serial number provided and matches a sold receiver
                if serial_number:
                    try:
                        # Find sale item with this serial number
                        sale_item = SaleItem.objects.filter(
                            serial_number__icontains=serial_number
                        ).first()
                        
                        if sale_item:
                            # Get the sale and customer
                            sale = sale_item.sale
                            
                            # Find customer
                            customer = Customer.objects.filter(
                                Q(name__iexact=sale.name) | 
                                Q(phone__iexact=sale.phone)
                            ).first()
                            
                            if customer:
                                # Assign code
                                activation_code.receiver_serial = serial_number
                                activation_code.customer = customer
                                activation_code.sale = sale
                                activation_code.status = 'assigned'
                                activation_code.assigned_date = timezone.now()
                                activation_code.save()
                                
                                # Log assignment
                                CodeAssignmentLog.objects.create(
                                    code=activation_code,
                                    receiver_serial=serial_number,
                                    customer=customer,
                                    sale=sale,
                                    assigned_by=request.user,
                                    notes="Auto-assigned during import"
                                )
                                
                                assigned_count += 1
                    except Exception as e:
                        print(f"Error auto-assigning code {code}: {str(e)}")
                        continue
            
            return Response({
                "success": True,
                "message": f"Imported {imported_count} codes. Auto-assigned {assigned_count} codes.",
                "batch_id": batch.id,
                "batch_number": batch.batch_number
            })
            
        except Exception as e:
            return Response(
                {"error": f"Import failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 2. ASSIGN CODE TO RECEIVER
class AssignCodeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            receiver_serial = request.data.get('receiver_serial')
            code_id = request.data.get('code_id')
            customer_id = request.data.get('customer_id')
            sale_id = request.data.get('sale_id')
            
            if not receiver_serial or not code_id:
                return Response(
                    {"error": "Receiver serial and code ID are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get code
            code = ActivationCode.objects.get(id=code_id, status='available')
            
            # Get customer and sale
            customer = None
            sale = None
            
            if customer_id:
                customer = Customer.objects.get(id=customer_id)
            
            if sale_id:
                sale = Sale.objects.get(id=sale_id)
            
            # Find customer from serial if not provided
            if not customer:
                # Try to find sale item with this serial
                sale_item = SaleItem.objects.filter(
                    serial_number__icontains=receiver_serial
                ).first()
                
                if sale_item and sale_item.sale:
                    sale = sale_item.sale
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale.name) | 
                        Q(phone__iexact=sale.phone)
                    ).first()
            
            # Assign code
            code.receiver_serial = receiver_serial
            code.customer = customer
            code.sale = sale
            code.status = 'assigned'
            code.assigned_date = timezone.now()
            code.save()
            
            # Log assignment
            CodeAssignmentLog.objects.create(
                code=code,
                receiver_serial=receiver_serial,
                customer=customer,
                sale=sale,
                assigned_by=request.user
            )
            
            return Response({
                "success": True,
                "message": f"Code {code.code} assigned to receiver {receiver_serial}",
                "code": ActivationCodeSerializer(code).data
            })
            
        except ActivationCode.DoesNotExist:
            return Response(
                {"error": "Code not found or not available"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Assignment failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 3. GET CUSTOMER CODES
class CustomerCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        try:
            user = request.user
            receiver_serial = request.query_params.get('receiver_serial')
            
            if user.role == 'customer':
                # Get customer's codes
                customer = Customer.objects.get(user=user)
                codes = ActivationCode.objects.filter(
                    customer=customer,
                    status='assigned'
                ).order_by('-assigned_date')
                
                # Filter by serial if provided
                if receiver_serial:
                    codes = codes.filter(receiver_serial__icontains=receiver_serial)
                
                # Check payment status for each receiver
                result = []
                for code in codes:
                    # Get payment info for this receiver
                    last_payment = Payment.objects.filter(
                        customer=user,
                        sale=code.sale
                    ).order_by('-payment_date').first()
                    
                    months_since_payment = None
                    if last_payment:
                        months_since_payment = (timezone.now() - last_payment.payment_date).days // 30
                    
                    # Check eligibility
                    eligible_for_regular = months_since_payment is not None and months_since_payment <= 4
                    
                    result.append({
                        **ActivationCodeSerializer(code).data,
                        'eligible_for_regular': eligible_for_regular,
                        'months_since_last_payment': months_since_payment,
                        'requires_payment': not eligible_for_regular and code.is_emergency,
                        'can_request_emergency': not eligible_for_regular
                    })
                
                return Response(result)
            
            elif user.role in ['admin', 'staff']:
                # Admin can query any customer
                customer_id = request.query_params.get('customer_id')
                receiver_serial = request.query_params.get('receiver_serial')
                
                if customer_id:
                    customer = Customer.objects.get(id=customer_id)
                    codes = ActivationCode.objects.filter(customer=customer)
                elif receiver_serial:
                    codes = ActivationCode.objects.filter(receiver_serial__icontains=receiver_serial)
                else:
                    return Response(
                        {"error": "Provide customer_id or receiver_serial for admin query"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                return Response(ActivationCodeSerializer(codes, many=True).data)
            
            return Response([])
            
        except Customer.DoesNotExist:
            return Response(
                {"error": "Customer not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 4. GENERATE EMERGENCY CODE
class GenerateEmergencyCodeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def post(self, request):
        try:
            receiver_serial = request.data.get('receiver_serial')
            customer_id = request.data.get('customer_id')
            
            if not receiver_serial:
                return Response(
                    {"error": "Receiver serial is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Find available 2-week code
            emergency_code = ActivationCode.objects.filter(
                duration='2weeks',
                status='available',
                is_emergency=True
            ).first()
            
            if not emergency_code:
                return Response(
                    {"error": "No emergency codes available"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get customer
            customer = None
            if customer_id:
                customer = Customer.objects.get(id=customer_id)
            else:
                # Try to find customer from serial
                sale_item = SaleItem.objects.filter(
                    serial_number__icontains=receiver_serial
                ).first()
                
                if sale_item and sale_item.sale:
                    sale = sale_item.sale
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale.name) | 
                        Q(phone__iexact=sale.phone)
                    ).first()
            
            # Assign emergency code
            emergency_code.receiver_serial = receiver_serial
            emergency_code.customer = customer
            emergency_code.status = 'assigned'
            emergency_code.assigned_date = timezone.now()
            emergency_code.save()
            
            # Log assignment
            CodeAssignmentLog.objects.create(
                code=emergency_code,
                receiver_serial=receiver_serial,
                customer=customer,
                assigned_by=request.user,
                notes="Emergency code generated"
            )
            
            return Response({
                "success": True,
                "message": f"Emergency 2-week code generated for {receiver_serial}",
                "code": emergency_code.code,
                "expiry_date": emergency_code.expiry_date
            })
            
        except Exception as e:
            return Response(
                {"error": f"Failed to generate emergency code: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# 5. AVAILABLE CODES VIEW (for admin)
class AvailableCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def get(self, request):
        duration = request.query_params.get('duration')
        
        codes = ActivationCode.objects.filter(status='available')
        
        if duration:
            codes = codes.filter(duration=duration)
        
        # Count by duration
        counts = codes.values('duration').annotate(count=Count('id'))
        
        return Response({
            'codes': ActivationCodeSerializer(codes, many=True).data,
            'counts': list(counts),
            'total_available': codes.count()
        })


# 6. RECEIVERS NEEDING CODES
class ReceiversNeedingCodesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrStaff]
    
    def get(self, request):
        # Find receivers sold but without codes or with expired codes
        receivers_needing_codes = []
        
        # Get all sale items that are receivers
        receiver_sales = SaleItem.objects.filter(
            tool__category='Receiver'
        ).select_related('sale', 'tool')
        
        for sale_item in receiver_sales:
            # Get serial number(s)
            serials = []
            if sale_item.serial_number:
                # Check if it's a JSON array or single serial
                try:
                    serials_data = json.loads(sale_item.serial_number)
                    if isinstance(serials_data, list):
                        serials = serials_data
                    else:
                        serials = [serials_data]
                except:
                    serials = [sale_item.serial_number]
            
            # Check each serial
            for serial in serials:
                # Check if code exists for this serial
                code_exists = ActivationCode.objects.filter(
                    receiver_serial=serial,
                    status='assigned'
                ).exists()
                
                # Check if existing code is expired
                expired_code = ActivationCode.objects.filter(
                    receiver_serial=serial,
                    status='assigned'
                ).first()
                
                is_expired = expired_code and expired_code.is_expired if expired_code else False
                
                if not code_exists or is_expired:
                    # Find customer
                    customer = Customer.objects.filter(
                        Q(name__iexact=sale_item.sale.name) | 
                        Q(phone__iexact=sale_item.sale.phone)
                    ).first()
                    
                    receivers_needing_codes.append({
                        'serial': serial,
                        'customer_id': customer.id if customer else None,
                        'customer_name': customer.name if customer else sale_item.sale.name,
                        'sale_id': sale_item.sale.id,
                        'sale_invoice': sale_item.sale.invoice_number,
                        'last_payment_date': customer.date_last_paid if customer else None,
                        'needs_urgent': is_expired,  # Expired = urgent
                        'has_code': code_exists,
                        'code_expired': is_expired
                    })
        
        return Response(receivers_needing_codes)