bl_info = {
    "name": "BatchRenderer",
    "author": "Rafí Mota",
    "version": (1, 0),
    "blender": (5, 0, 1),
    "location": "View3D > UI > BatchRenderer",
    "description": "Mass render tool with palette, rotation, and camera controls",
    "category": "Render",
}

import bpy
import os
import re
import math
import random
import time
from mathutils import Matrix, Vector

# =========================================================================
# CONFIGURATION & CONSTANTS
# =========================================================================

# If PIL is not installed, image resizing will fallback or skip
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Default Paths (can be overridden via the addon UI)
DEFAULT_RENDER_PATH_TEMP = r"C:\Users\Notebook\Desktop\Renders\TEMP"
DEFAULT_OUTPUT_BASE_FOLDER = r"C:\Users\Notebook\Desktop\Renders\Redimensionadas"
EXCLUDE_ROTATION_NAMES = {"Background"}

# Regex
MATERIAL_PATTERN = re.compile(r"^(\d+)\.(\d+)\s*-\s*(.+)$")
OBJECT_PATTERN = re.compile(r"^(\d+)\s*-\s*")

# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def ensure_folders(temp_path, output_path):
    os.makedirs(temp_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)

def get_palettes_data():
    """Scans materials to find palettes and color names."""
    paletas = {}       # { paleta_id: { obj_id: material } }
    nomes_cores = {}   # { paleta_id: [color_names] }

    for mat in bpy.data.materials:
        m = MATERIAL_PATTERN.match(mat.name)
        if m:
            paleta_id, obj_id, cor_nome = m.groups()
            paletas.setdefault(paleta_id, {})[obj_id] = mat
            nomes_cores.setdefault(paleta_id, [])
            nome = cor_nome.strip()
            if nome not in nomes_cores[paleta_id]:
                nomes_cores[paleta_id].append(nome)
    return paletas, nomes_cores

def get_grouped_objects():
    """Groups objects by ID (e.g. '1 - Body')."""
    objetos_por_id = {}
    for obj in bpy.data.objects:
        mo = OBJECT_PATTERN.match(obj.name)
        if mo:
            obj_id = mo.group(1)
            objetos_por_id.setdefault(obj_id, []).append(obj)
    return objetos_por_id

def get_grouped_collections():
    """Groups collections by ID (e.g. '1 - CollectionName').
    Returns { collection_id: [list of mesh objects inside that collection] }.
    """
    collections_por_id = {}
    for col in bpy.data.collections:
        mo = OBJECT_PATTERN.match(col.name)
        if mo:
            col_id = mo.group(1)
            # Gather all mesh objects recursively within this collection
            mesh_objs = _get_mesh_objects_recursive(col)
            if mesh_objs:
                collections_por_id.setdefault(col_id, []).extend(mesh_objs)
    return collections_por_id

def _get_mesh_objects_recursive(collection):
    """Recursively collects all MESH objects from a collection and its children."""
    result = []
    for obj in collection.objects:
        if obj.type == 'MESH':
            result.append(obj)
    for child_col in collection.children:
        result.extend(_get_mesh_objects_recursive(child_col))
    return result

def parse_input_list(text):
    """
    Parses '1-5', '1,3', 'cam' etc.
    Returns: 'cam' OR list of string IDs.
    """
    t = text.strip()
    if not t: return "cam"
    t_low = t.lower()
    if t_low == "cam": return "cam"
    
    ids = set()
    parts = t.split(',')
    for p in parts:
        p = p.strip()
        if '-' in p:
            try:
                start, end = p.split('-')
                for i in range(int(start), int(end) + 1):
                    ids.add(str(i))
            except ValueError:
                pass
        elif p:
            ids.add(p)
    return sorted(list(ids), key=lambda x: int(x) if x.isdigit() else 9999)

def parse_angles(text):
    """Parses '135, 225' into [135.0, 225.0]."""
    if not text.strip():
        return []
    try:
        return [float(x.strip()) for x in text.split(',') if x.strip()]
    except ValueError:
        return []

def sanitizar_nome(nome):
    for ch in '<>:"/\\|?*':
        nome = nome.replace(ch, "_")
    return nome

def gerar_hash():
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return f"#{''.join(random.choice(chars) for _ in range(3))}"

def apply_palette(palette_id, paletas_data, objetos_por_id, collections_por_id=None, mode='OBJECTS'):
    """Apply materials from a palette to objects.
    
    Args:
        palette_id: The palette ID string (e.g. '1')
        paletas_data: Dict of { palette_id: { obj_id: material } }
        objetos_por_id: Dict of { obj_id: [objects] } from object names
        collections_por_id: Dict of { col_id: [objects] } from collection names
        mode: 'OBJECTS' (by object name), 'COLLECTIONS' (by collection), or 'BOTH'
    """
    materiais_map = paletas_data.get(palette_id, {})
    
    # Single-color palette: if only 1 material exists, apply it to everything
    is_single_color = (len(materiais_map) == 1)
    if is_single_color:
        single_mat = list(materiais_map.values())[0]
    
    # Apply by object name
    if mode in ('OBJECTS', 'BOTH'):
        for obj_id, objs in objetos_por_id.items():
            if is_single_color:
                mat = single_mat
            else:
                mat = materiais_map.get(obj_id)
            if not mat:
                continue
            for obj in objs:
                if obj.type == 'MESH':
                    _assign_material(obj, mat)
    
    # Apply by collection
    if mode in ('COLLECTIONS', 'BOTH') and collections_por_id:
        for col_id, objs in collections_por_id.items():
            if is_single_color:
                mat = single_mat
            else:
                mat = materiais_map.get(col_id)
            if not mat:
                continue
            for obj in objs:
                _assign_material(obj, mat)


def _assign_material(obj, mat):
    """Assign a material to a mesh object's first slot."""
    if not obj.data.materials:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

# =========================================================================
# IMAGE PROCESSING
# =========================================================================

def resize_image_to_1000(img_path, dest_dir, novo_nome=None):
    base = os.path.basename(img_path)
    nome = novo_nome if novo_nome else base
    root, ext = os.path.splitext(nome)
    
    # Preserve or generate hash
    hash_match = re.search(r'\s*-\s*#[0-9a-zA-Z]{3}$', root)
    if hash_match:
        hash_str = hash_match.group(0).strip()
        root = root[:hash_match.start()].rstrip()
    else:
        hash_str = f" - {gerar_hash()}"
    
    out_path = os.path.join(dest_dir, f"{root}_1000x1000{hash_str}{ext}")

    success = False
    if PIL_AVAILABLE:
        try:
            with Image.open(img_path) as img:
                resampled = img.resize((1000, 1000), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
                resampled.save(out_path)
                success = True
        except Exception as e:
            print(f"DEBUG: PIL resize failed: {e}")

    if not success:
        # Fallback to Blender internal
        try:
            b_img = bpy.data.images.load(img_path)
            try:
                b_img.scale(1000, 1000)
                ext_low = ext.lower()
                b_img.file_format = 'PNG' if ext_low == '.png' else 'JPEG'
                if ext_low not in ['.png', '.jpg', '.jpeg']:
                    out_path = os.path.join(dest_dir, f"{root}_1000x1000{hash_str}.png")
                    b_img.file_format = 'PNG'
                
                b_img.filepath_raw = out_path
                b_img.save()
            finally:
                bpy.data.images.remove(b_img)
        except Exception as e:
            print(f"DEBUG: Blender fallback resize failed: {e}")

# =========================================================================
# ROTATION LOGIC
# =========================================================================

def get_pivot(context, use_cursor, objects):
    if use_cursor:
        try:
            return Vector(context.scene.cursor.location)
        except:
            return Vector((0,0,0))
    else:
        # Calculate median center
        if not objects: return Vector((0,0,0))
        valid_objs = [o for o in objects if o]
        if not valid_objs: return Vector((0,0,0))
        return sum((o.matrix_world.translation for o in valid_objs), Vector()) / len(valid_objs)

def rotate_objects_z(objects, degrees, pivot):
    if abs(degrees) < 1e-6: return
    rad = math.radians(degrees)
    R = Matrix.Rotation(rad, 4, 'Z')
    T_neg = Matrix.Translation(-pivot)
    T_pos = Matrix.Translation(pivot)
    M = T_pos @ R @ T_neg
    
    for o in objects:
        o.matrix_world = M @ o.matrix_world

# =========================================================================
# CORE RENDER ROUTINE
# =========================================================================

def run_render_process(context, settings):
    # Resolve folder paths from settings (fall back to defaults if empty)
    temp_path = bpy.path.abspath(settings.render_path_temp) if settings.render_path_temp.strip() else DEFAULT_RENDER_PATH_TEMP
    output_path = bpy.path.abspath(settings.output_base_folder) if settings.output_base_folder.strip() else DEFAULT_OUTPUT_BASE_FOLDER
    ensure_folders(temp_path, output_path)
    scene = context.scene
    
    # 0. Interpret Settings
    width = settings.res_x
    height = settings.res_y
    use_cursor = settings.use_cursor_pivot
    do_rotation = settings.enable_rotation
    user_angles = parse_angles(settings.rotation_angles) if do_rotation else []
    
    # Standard: [0] + user_angles. 
    # Logic: Always render at 0 first, then rotate by difference.
    # If user types "135", we render 0, then 135.
    render_angles = [0.0] + user_angles if do_rotation else [0.0]

    # 1. Identify Target Objects (Methods)
    objects_to_rotate = [
        o for o in scene.objects 
        if o.type == 'MESH' and o.name not in EXCLUDE_ROTATION_NAMES
    ]
    
    # 2. Identify Cameras
    # Filter only selected cameras from UI
    selected_cams_names = {item.name for item in scene.batch_renderer_cameras if item.selected}
    valid_cameras = [o for o in scene.objects if o.type == 'CAMERA' and o.name in selected_cams_names]
    
    if not valid_cameras:
        return "No cameras selected!"

    # 3. Data Prep
    paletas_data, nomes_cores = get_palettes_data()
    assign_mode = settings.material_assign_mode
    objetos_por_id = get_grouped_objects() if assign_mode in ('OBJECTS', 'BOTH') else {}
    collections_por_id = get_grouped_collections() if assign_mode in ('COLLECTIONS', 'BOTH') else {}
    
    selection_input = settings.palette_input
    palette_ids = parse_input_list(selection_input)
    
    mode_cam_only = (palette_ids == "cam")
    
    # If Palette Mode but valid IDs found
    if not mode_cam_only and isinstance(palette_ids, list):
        # Filter existing palettes
        palette_ids = [pid for pid in palette_ids if pid in paletas_data]
        if not palette_ids:
            return "No valid palettes found for input."

    # Setup Render
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    
    total_rendered = 0
    start_time = time.time()
    
    # --- HELPER: Render Sequence for Current State ---
    def execute_render_sequence(base_name_prefix, output_dir):
        # Capture initial state
        initial_matrices = {o.name: o.matrix_world.copy() for o in objects_to_rotate}
        pivot = get_pivot(context, use_cursor, objects_to_rotate)
        
        generated_files = []
        current_angle = 0.0
        
        for target_angle in render_angles:
            # Rotate Difference
            delta = target_angle - current_angle
            if abs(delta) > 1e-6:
                rotate_objects_z(objects_to_rotate, delta, pivot)
                context.view_layer.update()
                current_angle = target_angle
            
            # Render
            ang_int = int(round(target_angle))
            fname = f"{base_name_prefix}_{ang_int:03d} - {gerar_hash()}.png"
            full_path = os.path.join(temp_path, fname)
            
            scene.render.filepath = full_path
            bpy.ops.render.render(write_still=True)
            generated_files.append(full_path)
            
        # Restore state
        for o in objects_to_rotate:
            if o.name in initial_matrices:
                o.matrix_world = initial_matrices[o.name]
        context.view_layer.update()
        
        # Resize/Move & optional TEMP cleanup
        for fpath in generated_files:
            if os.path.exists(fpath):
                if settings.create_downscaled:
                    resize_image_to_1000(fpath, output_dir)
                # Cleanup temp file if requested
                if settings.cleanup_temp:
                    try:
                        os.remove(fpath)
                    except OSError as e:
                        print(f"DEBUG: Could not remove temp file {fpath}: {e}")

    # --- EXECUTION LOOP ---
    
    if mode_cam_only:
        dest_dir = os.path.join(output_path, "Cameras")
        os.makedirs(dest_dir, exist_ok=True)
        
        for cam in valid_cameras:
            scene.camera = cam
            base_name = f"CamOnly_{cam.name}"
            execute_render_sequence(base_name, dest_dir)
            
    else:
        # Palette Mode
        for pid in palette_ids:
            # Apply Material
            apply_palette(pid, paletas_data, objetos_por_id, collections_por_id, assign_mode)
            
            # Determine Output Folder
            p_colors = nomes_cores.get(pid, [])
            folder_name = "Paleta - " + "_".join(p_colors) if p_colors else f"Paleta - {pid}"
            folder_name = sanitizar_nome(folder_name)
            dest_dir = os.path.join(output_path, folder_name)
            os.makedirs(dest_dir, exist_ok=True)
            
            for cam in valid_cameras:
                scene.camera = cam
                base_name = f"Paleta{pid}_{cam.name}"
                execute_render_sequence(base_name, dest_dir)
                
            total_rendered += 1

    elapsed_total = time.time() - start_time
    mins, rem = divmod(elapsed_total, 60)
    secs = int(rem)
    msecs = int((rem - secs) * 1000)
    
    return (f"Done. Rendered in {int(mins)}m {secs}s {msecs}ms  |  "
            f"{total_rendered} palettes / {len(valid_cameras)} cameras.")


# =========================================================================
# UI CLASSES
# =========================================================================

class SC_CameraItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Camera Name")
    selected: bpy.props.BoolProperty(name="Select", default=True)

class SC_OT_RefreshCameras(bpy.types.Operator):
    bl_idname = "batchrenderer.refresh_cameras"
    bl_label = "Refresh Camera List"
    bl_description = "Populate list with current scene cameras"

    def execute(self, context):
        context.scene.batch_renderer_cameras.clear()
        # Find all cameras in scene objects
        cams = [o for o in context.scene.objects if o.type == 'CAMERA']
        for c in cams:
            item = context.scene.batch_renderer_cameras.add()
            item.name = c.name
            item.selected = True
        return {'FINISHED'}

class SC_OT_SelectAllCameras(bpy.types.Operator):
    bl_idname = "batchrenderer.select_all_cameras"
    bl_label = "Select All"
    bl_description = "Select all cameras in the list"

    def execute(self, context):
        for item in context.scene.batch_renderer_cameras:
            item.selected = True
        return {'FINISHED'}

class SC_OT_DeselectAllCameras(bpy.types.Operator):
    bl_idname = "batchrenderer.deselect_all_cameras"
    bl_label = "Deselect All"
    bl_description = "Deselect all cameras in the list"

    def execute(self, context):
        for item in context.scene.batch_renderer_cameras:
            item.selected = False
        return {'FINISHED'}

class SC_OT_Render(bpy.types.Operator):
    bl_idname = "batchrenderer.render_trigger"
    bl_label = "Start Render Batch"
    bl_description = "Starts the rendering process with current settings"
    
    def execute(self, context):
        msg = run_render_process(context, context.scene.batch_renderer_settings)
        self.report({'INFO'}, msg)
        return {'FINISHED'}

# Aspect Ratio helpers
ASPECT_RATIO_ITEMS = [
    ('FREEFORM', 'Freeform', 'No aspect ratio lock – set width and height independently'),
    ('1_1',  '1:1',  'Square – equal width and height'),
    ('3_4',  '3:4',  'Portrait 3:4'),
    ('4_5',  '4:5',  'Portrait 4:5'),
    ('9_16', '9:16', 'Portrait 9:16'),
    ('16_9', '16:9', 'Landscape 16:9'),
]

ASPECT_RATIOS = {
    '1_1':  (1, 1),
    '3_4':  (3, 4),
    '4_5':  (4, 5),
    '9_16': (9, 16),
    '16_9': (16, 9),
}

def _update_resolution(self, context):
    """Recalculate res_y when aspect_ratio or res_x changes."""
    ratio = self.aspect_ratio
    if ratio != 'FREEFORM' and ratio in ASPECT_RATIOS:
        w, h = ASPECT_RATIOS[ratio]
        self['res_y'] = int(self.res_x * h / w)


MATERIAL_ASSIGN_ITEMS = [
    ('OBJECTS',     'Object Names',  'Assign materials based on object names (e.g. "1 - Body")'),
    ('COLLECTIONS', 'Collections',  'Assign materials based on collection names (e.g. "1 - CollectionName")'),
    ('BOTH',        'Both',         'Assign materials using both object names and collection names'),
]


class SC_Settings(bpy.types.PropertyGroup):
    palette_input: bpy.props.StringProperty(
        name="Palettes",
        description="IDs like '1,3', '1-5', or 'cam' for camera mode",
        default="cam"
    )
    material_assign_mode: bpy.props.EnumProperty(
        name="Assign By",
        description="How materials from palettes are matched to objects",
        items=MATERIAL_ASSIGN_ITEMS,
        default='COLLECTIONS',
    )
    aspect_ratio: bpy.props.EnumProperty(
        name="Aspect Ratio",
        description="Lock resolution to a specific aspect ratio",
        items=ASPECT_RATIO_ITEMS,
        default='FREEFORM',
        update=_update_resolution,
    )
    res_x: bpy.props.IntProperty(
        name="Width", default=2200, min=1,
        update=_update_resolution,
    )
    res_y: bpy.props.IntProperty(name="Height", default=2200, min=1)

    create_downscaled: bpy.props.BoolProperty(
        name="Create 1000×1000 Version",
        description="Also save a downscaled 1000×1000 copy of each render",
        default=True,
    )
    cleanup_temp: bpy.props.BoolProperty(
        name="Cleanup Temp Files",
        description="Delete raw renders from the TEMP folder after processing",
        default=False,
    )

    enable_rotation: bpy.props.BoolProperty(name="Enable Rotation", default=False)
    rotation_angles: bpy.props.StringProperty(
        name="Angles (deg)",
        description="Comma separated angles relative to start (e.g. 135, 225)",
        default="135, 225"
    )
    use_cursor_pivot: bpy.props.BoolProperty(
        name="Use 3D Cursor Pivot",
        default=True,
        description="If True, rotates around 3D Cursor. If False, rotates around objects center."
    )

    # Custom Folders
    render_path_temp: bpy.props.StringProperty(
        name="Temp Folder",
        description="Folder for temporary full-resolution renders. Leave empty for default",
        default=r"C:\Users\Notebook\Desktop\Renders\TEMP",
        subtype='DIR_PATH',
    )
    output_base_folder: bpy.props.StringProperty(
        name="Output Folder",
        description="Folder for final resized renders. Leave empty for default",
        default=r"C:\Users\Notebook\Desktop\Renders\Redimensionadas",
        subtype='DIR_PATH',
    )

class VIEW3D_PT_BatchRendererPanel(bpy.types.Panel):
    bl_label = "BatchRenderer Render"
    bl_idname = "VIEW3D_PT_batchrenderer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BatchRenderer'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        sts = scene.batch_renderer_settings

        # Settings
        box = layout.box()
        box.label(text="Render Settings")
        box.prop(sts, "palette_input")
        box.prop(sts, "material_assign_mode")

        box.prop(sts, "aspect_ratio")

        row = box.row(align=True)
        row.prop(sts, "res_x")
        sub = row.row()
        sub.prop(sts, "res_y")
        # Disable manual height input when an aspect ratio is locked
        sub.enabled = (sts.aspect_ratio == 'FREEFORM')

        box.prop(sts, "create_downscaled")
        box.prop(sts, "cleanup_temp")

        # Custom Folders
        box_folders = layout.box()
        box_folders.label(text="Custom Folders", icon='FILE_FOLDER')
        box_folders.prop(sts, "render_path_temp")
        box_folders.prop(sts, "output_base_folder")
        
        # Rotation
        box_rot = layout.box()
        box_rot.prop(sts, "enable_rotation")
        if sts.enable_rotation:
            col = box_rot.column(align=True)
            col.prop(sts, "rotation_angles")
            col.prop(sts, "use_cursor_pivot")

        # Camera List
        box_cam = layout.box()
        row = box_cam.row()
        row.label(text="Cameras")
        row.operator("batchrenderer.refresh_cameras", icon='FILE_REFRESH', text="")

        # Select All / Deselect All buttons
        row_sel = box_cam.row(align=True)
        row_sel.operator("batchrenderer.select_all_cameras", icon='CHECKBOX_HLT')
        row_sel.operator("batchrenderer.deselect_all_cameras", icon='CHECKBOX_DEHLT')
        
        if len(scene.batch_renderer_cameras) > 0:
            col = box_cam.column(align=True)
            # Limit height if too many cameras
            col.scale_y = 0.9
            for idx, item in enumerate(scene.batch_renderer_cameras):
                row = col.row()
                row.prop(item, "selected", text="")
                row.label(text=item.name, icon='CAMERA_DATA')
        else:
            box_cam.label(text="No cameras found (Refresh)", icon='INFO')

        # Run
        layout.separator()
        layout.operator("batchrenderer.render_trigger", icon='RENDER_STILL')

# =========================================================================
# REGISTER
# =========================================================================

classes = (
    SC_CameraItem,
    SC_Settings,
    SC_OT_RefreshCameras,
    SC_OT_SelectAllCameras,
    SC_OT_DeselectAllCameras,
    SC_OT_Render,
    VIEW3D_PT_BatchRendererPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.batch_renderer_settings = bpy.props.PointerProperty(type=SC_Settings)
    bpy.types.Scene.batch_renderer_cameras = bpy.props.CollectionProperty(type=SC_CameraItem)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    del bpy.types.Scene.batch_renderer_settings
    del bpy.types.Scene.batch_renderer_cameras

if __name__ == "__main__":
    register()
