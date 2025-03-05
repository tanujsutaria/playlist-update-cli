import os
import pickle
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
from sklearn.feature_extraction.text import TfidfVectorizer
from models import Song

class DatabaseManager:
    """Manages the song database and embeddings using numpy for similarity search"""
    
    def __init__(self, data_dir: str = "data"):
        # Convert relative path to absolute path relative to the project root
        if not os.path.isabs(data_dir):
            # Get the directory where the script is located
            script_dir = Path(__file__).parent.parent  # Go up one level from src/
            self.data_dir = script_dir / data_dir
        else:
            self.data_dir = Path(data_dir)
        
        self.embeddings_dir = self.data_dir / "embeddings"
        self.embeddings_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Checking data directories:")
        print(f"- Data dir: {self.data_dir.absolute()}")
        print(f"- Embeddings dir: {self.embeddings_dir.absolute()}")
        
        # Check if files exist
        songs_path = self.embeddings_dir / "songs.pkl"
        embeddings_path = self.embeddings_dir / "embeddings.npy"
        print(f"\nChecking files:")
        print(f"- songs.pkl exists: {songs_path.exists()}")
        print(f"- embeddings.npy exists: {embeddings_path.exists()}")
        
        if not songs_path.exists() or not embeddings_path.exists():
            print(f"\nWARNING: Expected database files in: {self.embeddings_dir}")
            print(f"Current directory: {os.getcwd()}")
        
        print("\nInitializing embedding model...")
        self.model = TfidfVectorizer(stop_words='english')
        # Initialize the model with sample text to ensure non-empty vocabulary
        self.model.fit(["song artist music playlist track album"])
        
        self.songs = self._load_songs()
        print(f"\nLoaded {len(self.songs)} songs from database")
        
        self.embeddings = self._load_embeddings()
        print(f"Loaded embeddings shape: {self.embeddings.shape if len(self.embeddings) > 0 else 'empty'}")

    def _load_songs(self) -> dict:
        """Load song database from disk"""
        songs_path = self.embeddings_dir / "songs.pkl"
        if songs_path.exists():
            with open(songs_path, 'rb') as f:
                return pickle.load(f)
        return {}

    def _load_embeddings(self) -> np.ndarray:
        """Load embeddings from disk"""
        embeddings_path = self.embeddings_dir / "embeddings.npy"
        if embeddings_path.exists():
            return np.load(str(embeddings_path))
        return np.array([])

    def _save_state(self):
        """Save current state to disk"""
        # Save songs database
        with open(self.embeddings_dir / "songs.pkl", 'wb') as f:
            pickle.dump(self.songs, f)
        
        # Save embeddings array
        if len(self.embeddings) > 0:
            np.save(str(self.embeddings_dir / "embeddings.npy"), self.embeddings)

    def generate_embedding(self, song: Song) -> np.ndarray:
        """Generate embedding for a song"""
        text = f"{song.name} {song.artist}"
        
        # Check if we have existing embeddings to match dimensions
        if len(self.embeddings) > 0:
            expected_dim = self.embeddings.shape[1]
            
            # Load all existing songs to rebuild vocabulary consistently
            all_texts = []
            # Use at least 100 songs to build a stable vocabulary
            for i, song_id in enumerate(list(self.songs.keys())[:100]):
                s = self.songs[song_id]
                all_texts.append(f"{s.name} {s.artist}")
            
            # Add current text
            all_texts.append(text)
            
            # Always rebuild the model with all texts to ensure consistency
            self.model = TfidfVectorizer(stop_words='english')
            self.model.fit(all_texts)
            
            # Transform the text
            sparse_vector = self.model.transform([text])
            vector = sparse_vector.toarray()[0]
            
            # If dimensions still don't match, pad or truncate
            if len(vector) != expected_dim:
                if len(vector) < expected_dim:
                    # Pad with zeros
                    vector = np.pad(vector, (0, expected_dim - len(vector)))
                else:
                    # Truncate
                    vector = vector[:expected_dim]
            
            return vector
        else:
            # No existing embeddings, create a new model
            try:
                # For new databases, use a fixed dimension size of 384
                # This matches what seems to be your existing embedding size
                fixed_dim = 384
                
                # Create a basic vocabulary
                base_texts = [
                    text,
                    "song artist music playlist track album",
                    "rock pop jazz electronic indie alternative",
                    "vocals guitar drums bass piano keyboard",
                    "melody rhythm harmony tempo beat"
                ]
                
                # Create a new vectorizer with a fixed random state for consistency
                self.model = TfidfVectorizer(stop_words='english')
                self.model.fit(base_texts)
                
                # Transform the text
                sparse_vector = self.model.transform([text])
                vector = sparse_vector.toarray()[0]
                
                # Ensure the vector has the fixed dimension
                if len(vector) < fixed_dim:
                    vector = np.pad(vector, (0, fixed_dim - len(vector)))
                elif len(vector) > fixed_dim:
                    vector = vector[:fixed_dim]
                
                return vector
            except Exception as e:
                # Fallback to a zero vector of the right size if all else fails
                print(f"Error generating embedding: {str(e)}")
                return np.zeros(384)

    def add_song(self, song: Song) -> bool:
        """Add a song to the database"""
        if song.id in self.songs:
            return False

        # Set first_added if not already set
        if song.first_added is None:
            song.first_added = datetime.now()

        # Generate embedding if not provided
        if song.embedding is None:
            try:
                song.embedding = self.generate_embedding(song)
            except Exception as e:
                print(f"Error generating embedding for {song.name}: {str(e)}")
                # Create a zero vector of the right size
                if len(self.embeddings) > 0:
                    expected_dim = self.embeddings.shape[1]
                    song.embedding = np.zeros(expected_dim)
                else:
                    song.embedding = np.zeros(384)  # Default size

        # Add to embeddings array
        embedding = song.embedding.reshape(1, -1)
        if len(self.embeddings) == 0:
            self.embeddings = embedding
        else:
            try:
                self.embeddings = np.vstack([self.embeddings, embedding])
            except ValueError as e:
                print(f"Error adding embedding: {str(e)}")
                # Try to fix the embedding and try again
                if "shape" in str(e):
                    expected_dim = self.embeddings.shape[1]
                    if len(song.embedding) != expected_dim:
                        if len(song.embedding) < expected_dim:
                            song.embedding = np.pad(song.embedding, (0, expected_dim - len(song.embedding)))
                        else:
                            song.embedding = song.embedding[:expected_dim]
                        embedding = song.embedding.reshape(1, -1)
                        self.embeddings = np.vstack([self.embeddings, embedding])
        
        # Add to songs database
        self.songs[song.id] = song
        self._save_state()
        return True

    def get_all_songs(self) -> List[Song]:
        """Get all songs in the database"""
        return list(self.songs.values())

    def get_song_by_id(self, song_id: str) -> Optional[Song]:
        """Get a song by its ID"""
        return self.songs.get(song_id)

    def find_similar_songs(self, song: Song, k: int = 1, threshold: float = 0.9) -> List[Song]:
        """Find k most similar songs using cosine similarity"""
        if len(self.embeddings) == 0:
            return []

        if song.embedding is None:
            song.embedding = self.generate_embedding(song)

        # Calculate cosine similarity
        query_embedding = song.embedding.reshape(1, -1)
        # Handle zero vectors to avoid division by zero
        norms1 = np.linalg.norm(self.embeddings, axis=1)
        norms2 = np.linalg.norm(query_embedding)
        
        # Replace zero norms with a small value to avoid division by zero
        norms1[norms1 == 0] = 1e-10
        if norms2 == 0:
            norms2 = 1e-10
            
        similarities = np.dot(self.embeddings, query_embedding.T).flatten()
        similarities = similarities / (norms1 * norms2)
        
        # Get top k similar songs above threshold
        similar_indices = np.where(similarities > threshold)[0]
        similar_indices = similar_indices[np.argsort(-similarities[similar_indices])][:k]
        
        # Convert to song objects
        similar_songs = []
        song_ids = list(self.songs.keys())
        for idx in similar_indices:
            if idx < len(song_ids) and song_ids[idx] != song.id:
                similar_songs.append(self.songs[song_ids[idx]])
        
        return similar_songs

    def get_stats(self):
        """Get database statistics"""
        return {
            "total_songs": len(self.songs),
            "embedding_dimensions": self.embeddings.shape[1] if len(self.embeddings) > 0 else 0,
            "storage_size_mb": (self.embeddings.nbytes / 1024 / 1024) if len(self.embeddings) > 0 else 0
        } 
