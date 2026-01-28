"""
Debug utilities used by panels and operators.
"""
import bpy


def create_or_update_dummy_object(
    object_name: str,
    vertices: list,
    edges: list,
    location: tuple,
    rotation: tuple,
    collection: 'bpy.types.Collection'
) -> 'bpy.types.Object':
    """Create or update a dummy object with the given mesh and transform.
    Args:
        object_name: Name of the object to create/update
        vertices: List of vertex coordinates (tuples)
        edges: List of edge definitions (tuples of vertex indices)
        location: 3D location vector
        rotation: Rotation quaternion (w, x, y, z)
        collection: Collection to add the object to

    Returns:
        The created/updated object
    """
    # Create or get object
    if object_name in bpy.data.objects:
        dummy_obj = bpy.data.objects[object_name]
    else:
        mesh = bpy.data.meshes.new(f"{object_name}_mesh")
        dummy_obj = bpy.data.objects.new(object_name, mesh)

    # Update mesh geometry
    mesh = dummy_obj.data
    mesh.clear_geometry()
    mesh.from_pydata(vertices, edges, [])

    # Set transform
    dummy_obj.location = location
    dummy_obj.rotation_quaternion = rotation

    # Add to collection
    if dummy_obj.name not in collection.objects:
        collection.objects.link(dummy_obj)

    return dummy_obj
