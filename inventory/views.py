from django.db import models, transaction
from rest_framework import generics, permissions, status
from django.contrib.auth import get_user_model
from .models import Tool, Payment, Sale, Customer, EquipmentType, Supplier, SaleItem, CodeBatch, ActivationCode, CodeAssignmentLog, BatchSerial
from .serializers import (
    UserSerializer, ToolSerializer, EquipmentTypeSerializer,
    PaymentSerializer, SaleSerializer, CustomerSerializer, SupplierSerializer, CustomerOwingSerializer,
    CodeBatchSerializer, ActivationCodeSerializer, CodeAssignmentLogSerializer
)
from .permissions import IsAdminOrStaff, IsOwnerOrAdmin
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, Max, Q
from django.core.mail import send_mail, BadHeaderError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from datetime import timedelta, datetime
import secrets, uuid, traceback
import json
import pandas as pd
from io import BytesIO
from PIL import Image as PILImage
import csv, io
from django.http import HttpResponse
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as OpenpyxlImage
import base64
import random
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
# class ToolAssignRandomFromGroupView(APIView):
#     permission_classes = [permissions.IsAuthenticated]
    
#     def post(self, request):
#         tool_name = request.data.get('tool_name')
#         category = request.data.get('category')
        
#         if not tool_name:
#             return Response({"error": "Tool name is required."}, status=status.HTTP_400_BAD_REQUEST)
        
#         # Find all available tools with this name and category that have enough serials
#         available_tools = Tool.objects.filter(
#             name=tool_name, 
#             category=category,
#             stock__gt=0,
#             is_enabled=True
#         )
        
#         # === ADD YOUR COMBO LOGIC HERE ===
#         # Check if this is a combo equipment type
#         if "combo" in tool_name.lower():
#             # For combo, we need to assign both base and rover equipment
#             base_tools = Tool.objects.filter(
#                 name__icontains="base only",  # Adjust based on your actual tool names
#                 category=category,
#                 stock__gt=0,
#                 is_enabled=True
#             )
#             rover_tools = Tool.objects.filter(
#                 name__icontains="rover only",  # Adjust based on your actual tool names  
#                 category=category,
#                 stock__gt=0,
#                 is_enabled=True
#             )
            
#             if not base_tools or not rover_tools:
#                 return Response(
#                     {"error": "Complete base and rover sets not available for combo."},
#                     status=status.HTTP_404_NOT_FOUND
#                 )
            
#             # Get base serials (2 serials)
#             base_tool = base_tools.first()
#             base_serial_set = base_tool.get_random_serial_set()
#             if not base_serial_set or len(base_serial_set) != 2:
#                 return Response(
#                     {"error": "Failed to get complete base serial set."},
#                     status=status.HTTP_404_NOT_FOUND
#                 )
#             base_tool.decrease_stock()
            
#             # Get rover serials (2 serials)  
#             rover_tool = rover_tools.first()
#             rover_serial_set = rover_tool.get_random_serial_set()
#             if not rover_serial_set or len(rover_serial_set) != 2:
#                 return Response(
#                     {"error": "Failed to get complete rover serial set."},
#                     status=status.HTTP_404_NOT_FOUND
#                 )
#             rover_tool.decrease_stock()
            
#             # Combine all serials
#             all_serials = base_serial_set + rover_serial_set
            
#             return Response({
#                 "assigned_tool_id": f"combo_{base_tool.id}_{rover_tool.id}",
#                 "tool_name": tool_name,
#                 "serial_set": all_serials,  # This should be 4 serials total
#                 "serial_count": len(all_serials),
#                 "set_type": "Base & Rover Combo",
#                 "cost": str(float(base_tool.cost) + float(rover_tool.cost)),
#                 "description": "Base & Rover Combo Set",
#                 "remaining_stock": min(base_tool.stock, rover_tool.stock),
#                 "import_invoice": base_tool.invoice_number  # Use base tool invoice
#             })
        
#         # === NORMAL LOGIC FOR NON-COMBO EQUIPMENT ===
#         # Filter tools that have enough serials for a complete set
#         tools_with_enough_serials = []
#         for tool in available_tools:
#             set_count = tool.get_serial_set_count()
#             if tool.available_serials and len(tool.available_serials) >= set_count:
#                 tools_with_enough_serials.append(tool)
        
#         if not tools_with_enough_serials:
#             return Response(
#                 {"error": f"No complete {tool_name} sets available in stock."},
#                 status=status.HTTP_404_NOT_FOUND
#             )
        
#         # Select a random tool from available ones
#         import random
#         selected_tool = random.choice(tools_with_enough_serials)
        
#         # Get a complete serial SET (2 or 4 serials depending on equipment type)
#         serial_set = selected_tool.get_random_serial_set()
        
#         if not serial_set:
#             return Response(
#                 {"error": "Failed to get complete serial set from selected tool."},
#                 status=status.HTTP_404_NOT_FOUND
#             )
        
#         # Update the stock count
#         selected_tool.decrease_stock()
        
#         return Response({
#             "assigned_tool_id": selected_tool.id,
#             "tool_name": selected_tool.name,
#             "serial_set": serial_set,  # This is now an ARRAY of serials
#             "serial_count": len(serial_set),
#             "set_type": selected_tool.description,
#             "cost": str(selected_tool.cost),
#             "description": selected_tool.description,
#             "remaining_stock": selected_tool.stock,
#             "import_invoice": selected_tool.invoice_number
#         })

class ToolAssignRandomFromGroupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        tool_name = request.data.get('tool_name')
        category = request.data.get('category')
        requested_type = request.data.get('equipment_type', "").lower()
        
        if not tool_name:
            return Response({"error": "Tool name is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            items = list(Tool.objects.select_for_update().filter(
                name=tool_name, 
                category=category,
                stock__gt=0,
                is_enabled=True
            ))

            # ✅ HANDLE ACCESSORIES
            if category == "Accessory":
                if not items:
                    return Response({"error": f"No {tool_name} available."}, status=404)
                
                selected_tool = random.choice(items)
                
                if selected_tool.available_serials and len(selected_tool.available_serials) > 0:
                    serial = selected_tool.available_serials.pop(0)
                    selected_tool.stock -= 1
                    selected_tool.save()
                    
                    return Response({
                        "assigned_tool_id": selected_tool.id,
                        "tool_name": selected_tool.name,
                        "serial_set": [serial],
                        "serial_count": 1,
                        "set_type": "Accessory",
                        "cost": str(selected_tool.cost),
                        "description": selected_tool.description or "Accessory",
                        "remaining_stock": selected_tool.stock,
                        "import_invoice": selected_tool.invoice_number,
                        "datalogger_serial": None,
                        "external_radio_serial": None
                    })
                else:
                    return Response({"error": f"{tool_name} has no serials."}, status=404)

            # ✅ HANDLE RECEIVERS (Base, Rover, Combo)
            wants_combo = "combo" in requested_type or "base & rover" in requested_type
            selected_tool = None
            valid_serial_set = None
            
            random.shuffle(items) 

            for tool in items:
                needed_count = tool.get_serial_set_count()
                
                # STRICT UNIQUE SELECTION LOGIC
                # If combo is selected, it ONLY looks for tools with 4 or more serials.
                # If base/rover is selected, it ONLY looks for tools with less than 4 serials.
                if wants_combo:
                    if needed_count < 4: continue
                else:
                    if needed_count >= 4: continue

                if len(tool.available_serials or []) >= needed_count:
                    selected_tool = tool
                    valid_serial_set = tool.get_random_serial_set()
                    break

            if selected_tool and valid_serial_set:
                # Clean spaces but DO NOT pluck anything out. Send the full array.
                clean_serials = [s.strip() for s in valid_serial_set if s.strip()]

                return Response({
                    "assigned_tool_id": selected_tool.id,
                    "tool_name": selected_tool.name,
                    "serial_set": clean_serials,  # ✅ Full array (4 for combo, 2 for single)
                    "serial_count": len(clean_serials),
                    "set_type": selected_tool.description,
                    "cost": str(selected_tool.cost),
                    "description": selected_tool.description,
                    "remaining_stock": selected_tool.stock,
                    "import_invoice": selected_tool.invoice_number,
                    "datalogger_serial": None,
                    "external_radio_serial": None 
                })

        msg = f"Inventory Error: No available {tool_name} sets found for the selected type."
        return Response({"error": msg}, status=status.HTTP_404_NOT_FOUND)


# NEW: Support for restoring serials if the user cancels or removes an item
class ToolRestoreSerialsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        tool_id = request.data.get('tool_id')
        serial_set = request.data.get('serial_set')
        
        if not tool_id or not serial_set:
            return Response({"error": "Missing data"}, status=400)
            
        tool = get_object_or_404(Tool, id=tool_id)
        tool.restore_serials(serial_set)
        
        return Response({"message": "Stock restored successfully", "new_stock": tool.stock})


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
# class SaleListCreateView(generics.ListCreateAPIView):
#     serializer_class = SaleSerializer
#     permission_classes = [permissions.IsAuthenticated]

#     def get_queryset(self):
#         user = self.request.user
#         if user.role == "staff":
#             return Sale.objects.filter(staff=user).order_by("-date_sold")
#         elif user.role == "admin":
#             return Sale.objects.all().order_by("-date_sold")
#         return Sale.objects.none()

#     def perform_create(self, serializer):
#         # Get import_invoice from request data
#         import_invoice = self.request.data.get('import_invoice')
        
#         # Save the sale with staff and import_invoice
#         sale = serializer.save(
#             staff=self.request.user,
#             import_invoice=import_invoice
#         )
        
#         # FIXED: Also update sale items with serial_set data
#         if sale.items.exists():
#             items_data = self.request.data.get('items', [])
#             for i, item in enumerate(sale.items.all()):
#                 if i < len(items_data):
#                     item_data = items_data[i]
#                     # Save serial_set to the item
#                     serial_set = item_data.get('serial_set')
#                     if serial_set:
#                         # Convert serial_set array to serial_number field
#                         if isinstance(serial_set, list) and len(serial_set) > 0:
#                             if len(serial_set) == 1:
#                                 item.serial_number = serial_set[0]
#                             else:
#                                 item.serial_number = json.dumps(serial_set)
#                         item.save()
        
#         # Also update sale items with import_invoice if provided
#         if import_invoice and sale.items.exists():
#             sale.items.all().update(import_invoice=import_invoice)


# class SaleDetailView(generics.RetrieveUpdateDestroyAPIView):
#     serializer_class = SaleSerializer
#     permission_classes = [permissions.IsAuthenticated]

#     def get_queryset(self):
#         user = self.request.user
#         if user.role == "staff":
#             return Sale.objects.filter(staff=user)
#         elif user.role == "admin":
#             return Sale.objects.all()
#         return Sale.objects.none()

#     def perform_update(self, serializer):
#         user = self.request.user
#         instance = self.get_object()
#         if user.role == "staff" and instance.staff != user:
#             raise PermissionDenied("You can only edit your own sales.")
#         return super().perform_update(serializer)

class SaleListCreateView(generics.ListCreateAPIView):
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        
        # 1. Staff see their own sales
        if user.role == "staff":
            return Sale.objects.filter(staff=user).order_by("-date_sold")
            
        # 2. Admins see everything
        elif getattr(user, 'role', '') == "admin" or user.is_superuser:
            return Sale.objects.all().order_by("-date_sold")
            
        # 3. 🎯 CUSTOMERS see sales matching their name or phone!
        elif user.role == "customer":
            query = Q()
            if user.name:
                query |= Q(name__iexact=user.name)
            if user.phone:
                query |= Q(phone=user.phone)
                
            # Check Customer profile as fallback
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name:
                    query |= Q(name__iexact=user.customer.name)
                if user.customer.phone:
                    query |= Q(phone=user.customer.phone)
            
            if query:
                return Sale.objects.filter(query).order_by("-date_sold")
                
        # 4. Fallback for any unknown user type
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
        elif getattr(user, 'role', '') == "admin" or user.is_superuser:
            return Sale.objects.all()
        elif user.role == "customer":
            query = Q()
            if user.name:
                query |= Q(name__iexact=user.name)
            if user.phone:
                query |= Q(phone=user.phone)
                
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name:
                    query |= Q(name__iexact=user.customer.name)
                if user.customer.phone:
                    query |= Q(phone=user.customer.phone)
            
            if query:
                return Sale.objects.filter(query)
                
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


class PaymentSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        # ── Summary Cards ────────────────────────────────────────────────────
        all_sales = Sale.objects.all()

        total_revenue = all_sales.filter(
            payment_status="completed"
        ).aggregate(total=Sum("total_cost"))["total"] or 0

        pending_sales = all_sales.filter(
            payment_status__in=["pending", "installment"]
        )
        pending_amount = pending_sales.aggregate(
            total=Sum("total_cost")
        )["total"] or 0
        pending_count = pending_sales.count()

        overdue_customers = Customer.objects.filter(status="overdue")
        overdue_amount = overdue_customers.aggregate(
            total=Sum("amount_left")
        )["total"] or 0
        overdue_count = overdue_customers.count()

        # ── Payment History Table (one row per Sale) ──────────────────────────
        sales = Sale.objects.prefetch_related("items").order_by("-date_sold")

        rows = []
        for sale in sales:
            # Get first item's equipment name for display
            # first_item = sale.items.first()
            # equipment = first_item.equipment if first_item else "—"
            items_list = list(sale.items.values("equipment", "equipment_type"))

            rows.append({
                "payment_id":     sale.invoice_number,
                "invoice_number": sale.invoice_number,
                "customer_name":  sale.name,
                "customer_phone": sale.phone,
                "items":          items_list,
                "amount":         str(sale.total_cost),
                "date":           sale.date_sold.strftime("%Y-%m-%d") if sale.date_sold else "—",
                "payment_plan":   sale.payment_plan or "Full Payment",
                "payment_status": sale.payment_status,
                "state":          sale.state or "—",
            })

        return Response({
            "summary": {
                "total_revenue":   float(total_revenue),
                "pending_amount":  float(pending_amount),
                "pending_count":   pending_count,
                "overdue_amount":  float(overdue_amount),
                "overdue_count":   overdue_count,
                "total_sales":     all_sales.count(),
            },
            "payments": rows,
        }, status=status.HTTP_200_OK)

# ----------------------------
#  CODE MANAGEMENT VIEWS
# ----------------------------

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

# ---------------------------------------------------------
# DIRECT CODE MANAGEMENT (MANUAL EDITING)
# ---------------------------------------------------------

class ReceiverCodeManagementView(APIView):
    # Leave this as IsAuthenticated so both internal team and customers can hit the URL
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # 1. FIXED SECURITY CHECK: Allow both 'admin' and 'staff' to see everything
        is_internal_team = (
            user.is_staff or 
            user.is_superuser or 
            getattr(user, 'role', '') in ['admin', 'staff']
        )

        in_stock_data = []
        sold_data = []

        # --- 1. IN-STOCK RECEIVERS (Internal Team Only) ---
        if is_internal_team:
            in_stock_tools = Tool.objects.filter(category__icontains='Receiver', stock__gt=0)
            for tool in in_stock_tools:
                serials = tool.available_serials or []
                for serial in serials:
                    if any(x in serial.upper() for x in ["DL-", "ER-", "RADIO", "EXTERNAL"]):
                        continue

                    code_obj = ActivationCode.objects.filter(receiver_serial=serial).order_by('-id').first()
                    
                    in_stock_data.append({
                        "serial": serial,
                        "tool_name": tool.name,
                        "status": "In Stock",
                        "current_code": code_obj.code if code_obj else "",
                        "duration": code_obj.duration if code_obj else "",
                        "qr_code_image": code_obj.qr_code_image if code_obj else "",
                    })

        # --- 2. SOLD RECEIVERS (Team sees all, Customers see their own) ---
        if is_internal_team:
            # Admins and Staff see everything
            sold_items = SaleItem.objects.filter(tool__category__icontains='Receiver').select_related('sale', 'tool')
        else:
            # 2. FIXED CUSTOMER QUERY: Search by Name or Phone text fields
            query = Q()
            
            if user.name:
                query |= Q(sale__name__iexact=user.name)
            if user.phone:
                query |= Q(sale__phone=user.phone)
                
            # Fallback to Customer profile if it exists
            if hasattr(user, 'customer') and user.customer:
                if user.customer.name:
                    query |= Q(sale__name__iexact=user.customer.name)
                if user.customer.phone:
                    query |= Q(sale__phone=user.customer.phone)
            
            # If we don't know the user's name or phone, return an empty list
            if not query:
                sold_items = SaleItem.objects.none()
            else:
                sold_items = SaleItem.objects.filter(query, tool__category__icontains='Receiver').select_related('sale', 'tool')

        # --- PROCESS SOLD ITEMS ---
        for item in sold_items:
            serials = []
            if item.serial_number:
                try:
                    parsed = json.loads(item.serial_number)
                    serials = parsed if isinstance(parsed, list) else [item.serial_number]
                except:
                    serials = [item.serial_number]

            for serial in serials:
                if not serial or any(x in serial.upper() for x in ["DL-", "ER-", "RADIO", "EXTERNAL"]):
                    continue

                code_obj = ActivationCode.objects.filter(receiver_serial=serial).order_by('-id').first()
                
                sold_data.append({
                    "serial": serial,
                    "tool_name": item.tool.name,
                    "customer_name": item.sale.name if item.sale else "Unknown",
                    "invoice": item.sale.invoice_number if item.sale else "No Invoice",
                    "status": "Sold",
                    "current_code": code_obj.code if code_obj else "",
                    "duration": code_obj.duration if code_obj else "",
                    "qr_code_image": code_obj.qr_code_image if code_obj else "",
                })

        return Response({
            "in_stock": in_stock_data,
            "sold": sold_data
        }, status=status.HTTP_200_OK)

class SaveReceiverCodeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [permissions.IsAuthenticated] 

    def post(self, request):
        serial = request.data.get('serial')
        new_code = request.data.get('code')
        # REMOVED: duration = request.data.get('duration', 'unlimited')

        if not serial or not new_code:
            return Response({"error": "Serial and code are required."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. FIND THE SALE AND CUSTOMER
        sale_item = SaleItem.objects.filter(serial_number__icontains=serial).select_related('sale').first()
        
        customer_obj = None
        sale_obj = None

        if sale_item:
            sale_obj = sale_item.sale
            customer_obj = Customer.objects.filter(name=sale_item.sale.name).first()

        # 2. UPDATE OR CREATE ACTIVATION CODE (without duration)
        code_obj, created = ActivationCode.objects.update_or_create(
            receiver_serial=serial,
            defaults={
                "code": new_code,
                # REMOVED: "duration": duration,
                "customer": customer_obj,
                "sale": sale_obj,
                "status": 'assigned',
                "assigned_date": timezone.now(),
            }
        )

        # 3. ENSURE A BATCH EXISTS
        if created or not code_obj.batch:
            manual_batch, _ = CodeBatch.objects.get_or_create(
                batch_number="MANUAL-ENTRY",
                defaults={"supplier": "Manual Entry", "notes": "Codes entered manually via Code Management"}
            )
            code_obj.batch = manual_batch
            code_obj.save()

        return Response({
            "message": "Code saved successfully",
            "linked_to": customer_obj.name if customer_obj else "No Customer Found"
        }, status=status.HTTP_200_OK)
    

# ─────────────────────────────────────────────────────────────────────────────
#  1. BATCH LIST + CREATE
#     GET  /api/code-batches/
#     POST /api/code-batches/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        batches = CodeBatch.objects.annotate(
            code_count=Count("codes")
        ).order_by("-created_at")

        data = []
        for b in batches:
            in_stock_count = BatchSerial.objects.filter(batch=b, status="not sold").count()
            sold_count     = BatchSerial.objects.filter(batch=b, status="active").count()
            data.append({
                "id":             b.id,
                "batch_number":   b.batch_number,
                "received_date":  str(b.received_date),
                "supplier":       b.supplier,
                "notes":          b.notes or "",
                "code_count":     b.code_count,
                "in_stock_count": in_stock_count,
                "sold_count":     sold_count,
                "created_at":     b.created_at.isoformat(),
            })
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        batch_number = request.data.get("batch_number", "").strip()
        if not batch_number:
            return Response({"detail": "batch_number is required."}, status=status.HTTP_400_BAD_REQUEST)

        if CodeBatch.objects.filter(batch_number=batch_number).exists():
            return Response(
                {"detail": f"A batch named '{batch_number}' already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        batch = CodeBatch.objects.create(
            batch_number  = batch_number,
            supplier      = request.data.get("supplier", "China Supplier"),
            notes         = request.data.get("notes", ""),
            received_date = request.data.get("received_date") or timezone.now().date(),
        )
        return Response({
            "id":             batch.id,
            "batch_number":   batch.batch_number,
            "received_date":  str(batch.received_date),
            "supplier":       batch.supplier,
            "notes":          batch.notes or "",
            "code_count":     0,
            "in_stock_count": 0,
            "sold_count":     0,
        }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
#  2. BATCH ITEMS — in_stock / sold split for one batch
#     GET /api/code-batches/<pk>/items/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchItemsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        batch   = get_object_or_404(CodeBatch, pk=pk)
        serials = BatchSerial.objects.filter(batch=batch).order_by("serial_number")

        in_stock = []
        sold     = []

        for s in serials:
            # Look up activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            # Format expiry_date cleanly as DD/MM/YYYY
            expiry_display = ""
            if code_obj and code_obj.expiry_date:
                try:
                    expiry_display = code_obj.expiry_date.strftime("%d/%m/%Y")
                except Exception:
                    expiry_display = str(code_obj.expiry_date)[:10]

            row = {
                "serial":         s.serial_number,
                "status":         s.status,
                "payment_status": s.payment_status,
                "customer_name":  s.customer_name or "",
                "customer_email": s.customer_email or "",
                "assigned_date":  str(s.assigned_date) if s.assigned_date else "",
                "current_code":   code_obj.code if code_obj else "",
                "code_expiry":    expiry_display,
                "duration": f"{(code_obj.expiry_date.date() - timezone.now().date()).days} days" if (code_obj and code_obj.expiry_date) else "unlimited",
                "qr_code_image": code_obj.qr_code_image if code_obj else "",  # NEW
            }

            if s.status == "not sold":
                in_stock.append(row)
            else:
                sold.append(row)

        return Response({"in_stock": in_stock, "sold": sold}, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
#  3. CSV UPLOAD — auto-detects format A (supplier codes) or B (assignments)
#     POST /api/code-batches/<pk>/upload-csv/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchUploadCSVView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _parse_date(self, date_str):
        """Handle all date formats: datetime objects, Excel serials, and strings."""
        if not date_str:
            return None

        if hasattr(date_str, 'date'):
            return date_str.date()
        if hasattr(date_str, 'year'):
            return date_str

        date_str = str(date_str).strip()
        if not date_str or date_str.lower() in ('none', 'null', 'nan', ''):
            return None

        try:
            serial = int(float(date_str))
            if 20000 < serial < 70000:
                from datetime import date as date_type, timedelta
                return date_type(1899, 12, 30) + timedelta(days=serial)
        except (ValueError, TypeError):
            pass

        date_str = date_str.split(" ")[0].split("T")[0]

        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y",
                    "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        return None

    def _extract_qr_codes_from_excel(self, uploaded_file):
        """
        Extract QR code images from Excel file.
        Returns a dict mapping row numbers to base64-encoded images.
        """
        qr_codes = {}
        
        try:
            # Save uploaded file temporarily
            temp_file = BytesIO(uploaded_file.read())
            wb = load_workbook(temp_file)
            ws = wb.active
            
            # Excel images are stored in worksheet._images
            for img in ws._images:
                # Get the anchor position (which cell the image is in)
                if hasattr(img, 'anchor') and hasattr(img.anchor, '_from'):
                    row = img.anchor._from.row + 1  # Excel rows are 0-indexed in openpyxl
                    
                    # Convert image to base64
                    if hasattr(img, '_data'):
                        image_data = img._data()
                        
                        # Convert to PIL Image and then to base64
                        pil_image = PILImage.open(BytesIO(image_data))
                        
                        # Resize if too large (optional - for storage efficiency)
                        max_size = (300, 300)
                        pil_image.thumbnail(max_size, PILImage.Resampling.LANCZOS)
                        
                        # Convert to base64
                        buffered = BytesIO()
                        pil_image.save(buffered, format="PNG")
                        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                        
                        qr_codes[row] = img_base64
                        print(f"[QR Extract] Found QR code at row {row}")
            
            print(f"[QR Extract] Total QR codes extracted: {len(qr_codes)}")
            
        except Exception as e:
            print(f"[QR Extract] Error extracting QR codes: {e}")
            import traceback
            traceback.print_exc()
        
        return qr_codes

    def post(self, request, pk):
        batch = get_object_or_404(CodeBatch, pk=pk)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"detail": "No file uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        file_ext = uploaded_file.name.lower().split('.')[-1]
        if file_ext not in ['csv', 'xlsx', 'xls']:
            return Response(
                {"detail": "Only .csv, .xlsx, and .xls files are accepted."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Extract QR codes from Excel file (if it's Excel)
        qr_codes = {}
        if file_ext in ['xlsx', 'xls']:
            qr_codes = self._extract_qr_codes_from_excel(uploaded_file)
            uploaded_file.seek(0)  # Reset file pointer after extraction

        # Read file based on type
        if file_ext in ['xlsx', 'xls']:
            df = pd.read_excel(BytesIO(uploaded_file.read()))
            
            df.columns = [
                str(col).strip().lower().replace(" ", "_").replace("-", "_")
                for col in df.columns
            ]
            cols = set(df.columns)
            rows = df.to_dict('records')
            
            print(f"[Excel Upload] Batch={batch.batch_number} | Columns: {sorted(cols)}")
            
        else:
            # CSV logic
            raw = uploaded_file.read()
            try:
                decoded = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                decoded = raw.decode("latin-1")

            reader = csv.DictReader(io.StringIO(decoded))

            if not reader.fieldnames:
                return Response({"detail": "CSV appears to be empty."}, status=status.HTTP_400_BAD_REQUEST)

            reader.fieldnames = [
                f.strip().lower().replace(" ", "_").replace("-", "_")
                for f in reader.fieldnames
            ]
            cols = set(reader.fieldnames)
            rows = list(reader)
            
            print(f"[CSV Upload] Batch={batch.batch_number} | Columns: {sorted(cols)}")

        if "serial_number" not in cols and "sn" not in cols:
            return Response(
                {"detail": f"File must have a 'serial_number' or 'SN' column. Found: {sorted(cols)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Detect format
        is_supplier_format = bool(
            cols & {"current_code", "code", "activation_code", "activationcode", "temporary_code"}
        )
        print(f"[File Upload] Format: {'SUPPLIER (codes)' if is_supplier_format else 'ASSIGNMENT (customers)'}")

        imported = 0
        errors   = []

        if is_supplier_format:
            # FORMAT A: Process codes with QR codes
            for row_num, row in enumerate(rows, start=2):
                # Support both "serial_number" and "sn" columns
                serial = str(row.get("serial_number") or row.get("sn") or "").strip()

                # Support multiple code column names
                code = str(
                    row.get("code", "") or
                    row.get("current_code", "") or
                    row.get("activation_code", "") or
                    row.get("activationcode", "") or
                    row.get("temporary_code", "") or ""
                ).strip()

                if not serial or serial == 'nan':
                    errors.append(f"Row {row_num}: serial_number is empty — skipped.")
                    continue
                if not code or code == 'nan':
                    errors.append(f"Row {row_num} ({serial}): no code value found — skipped.")
                    continue

                expiry_raw = (
                    row.get("expiry_date", "") or
                    row.get("code_expiry", "") or
                    row.get("expiry", "") or
                    row.get("date_to", "") or ""
                )
                expiry_date = self._parse_date(expiry_raw)

                row_status        = str(row.get("status", "not sold")).strip().lower()
                row_payment       = str(row.get("payment_status", "not_applicable")).strip().lower()
                row_customer_email = str(row.get("customer_email", "")).strip()
                row_customer_name  = str(row.get("customer_name", "")).strip()
                row_assigned_date  = self._parse_date(row.get("assigned_date", ""))

                # Get QR code for this row (if available)
                qr_code_base64 = qr_codes.get(row_num, None)

                print(f"[File Row {row_num}] serial={serial} | code={code[:20]} | has_qr={bool(qr_code_base64)}")

                try:
                    # Save activation code with QR code
                    ActivationCode.objects.update_or_create(
                        receiver_serial=serial,
                        defaults={
                            "code":            code,
                            "batch":           batch,
                            "expiry_date":     expiry_date,
                            "status":          "assigned" if row_status == "active" else "available",
                            "assigned_date":   timezone.now() if row_status == "active" else None,
                            "qr_code_image":   qr_code_base64,  # NEW: Save QR code
                        }
                    )

                    # Update BatchSerial
                    BatchSerial.objects.update_or_create(
                        batch=batch,
                        serial_number=serial,
                        defaults={
                            "status":         row_status,
                            "payment_status": row_payment,
                            "customer_email": row_customer_email or None,
                            "customer_name":  row_customer_name or None,
                            "assigned_date":  row_assigned_date,
                        }
                    )

                    imported += 1

                except Exception as exc:
                    errors.append(f"Row {row_num} ({serial}): {exc}")
                    import traceback
                    traceback.print_exc()

        else:
            # FORMAT B: Assignment format (no changes needed here)
            BatchSerial.objects.filter(batch=batch).delete()

            for row_num, row in enumerate(rows, start=2):
                serial = str(row.get("serial_number") or row.get("sn") or "").strip()
                if not serial or serial == 'nan':
                    errors.append(f"Row {row_num}: serial_number is empty — skipped.")
                    continue

                raw_status     = str(row.get("status", "not sold")).strip().lower()
                payment_status = str(row.get("payment_status", "not_applicable")).strip().lower()
                customer_email = str(row.get("customer_email", "")).strip()
                customer_name  = str(row.get("customer_name", "")).strip()
                assigned_date  = self._parse_date(row.get("assigned_date", ""))

                try:
                    BatchSerial.objects.create(
                        batch          = batch,
                        serial_number  = serial,
                        status         = raw_status,
                        payment_status = payment_status,
                        customer_email = customer_email or None,
                        customer_name  = customer_name  or None,
                        assigned_date  = assigned_date,
                    )
                    imported += 1
                except Exception as exc:
                    errors.append(f"Row {row_num} ({serial}): {exc}")

        return Response({
            "imported": imported, 
            "errors": errors,
            "qr_codes_extracted": len(qr_codes)
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
#  4. CSV DOWNLOAD — exports batch serials + their codes back to CSV
#     GET /api/code-batches/<pk>/download-csv/
# ─────────────────────────────────────────────────────────────────────────────

class CodeBatchDownloadCSVView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        batch   = get_object_or_404(CodeBatch, pk=pk)
        serials = BatchSerial.objects.filter(batch=batch).order_by("serial_number")

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="batch_{batch.batch_number}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow([
            "serial_number", "current_code", "code_expiry",
            "status", "payment_status",
            "customer_email", "customer_name", "assigned_date",
        ])

        for s in serials:
            # Get activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            writer.writerow([
                s.serial_number,
                code_obj.code        if code_obj else "",
                code_obj.expiry_date.strftime("%d/%m/%Y") if (code_obj and code_obj.expiry_date) else "",
                s.status,
                s.payment_status,
                s.customer_email or "",
                s.customer_name  or "",
                s.assigned_date.strftime("%d/%m/%Y") if s.assigned_date else "",
            ])

        return response

class SendBulkExpirationEmailsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        batch = get_object_or_404(CodeBatch, pk=pk)
        
        # Get all sold/active serials that actually have an email address
        sold_serials = BatchSerial.objects.filter(
            batch=batch, 
            status__in=["active", "sold"] 
        ).exclude(customer_email="") 

        sent_count = 0
        failed_count = 0

        for s in sold_serials:
            # Look up the latest activation code for this serial
            code_obj = ActivationCode.objects.filter(
                receiver_serial=s.serial_number
            ).order_by("-id").first()

            # Skip if they don't have a code or an expiry date yet
            if not code_obj or not code_obj.expiry_date:
                continue

            try:
                # Format the date nicely
                expiry_display = code_obj.expiry_date.strftime("%d/%m/%Y")
                
                # Build the email
                customer_name = s.customer_name if s.customer_name else "Valued Customer"
                subject = "Action Required: Your Equipment Activation Code is Expiring Soon"
                
                message = (
                    f"Hello {customer_name},\n\n"
                    f"This is a friendly reminder regarding your equipment (Serial: {s.serial_number}).\n"
                    f"Your current activation code ({code_obj.code}) is set to expire on {expiry_display}.\n\n"
                    f"Please contact us to renew your activation code and ensure uninterrupted service.\n\n"
                    f"Best regards,\nYour Support Team"
                )

                # Send the email using Django's free built-in sender
                send_mail(
                    subject,
                    message,
                    'your-company@gmail.com',  # <-- Change to your sending email
                    [s.customer_email],
                    fail_silently=False,
                )
                sent_count += 1
                
            except Exception as e:
                print(f"Failed to send email to {s.customer_email}: {str(e)}")
                failed_count += 1

        return Response(
            {"sent": sent_count, "failed": failed_count}, 
            status=status.HTTP_200_OK
        )